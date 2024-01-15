from katacv.utils.related_pkgs.utility import *
from katacv.utils.related_pkgs.jax_flax_optax_orbax import *
from katacv.utils.detection import iou
from katacv.yolov5.parser import YOLOv5Args
from katacv.yolov5.train_state import TrainState, accumulate_grads

def BCE(logits, y, mask):
  return -(mask * (
    y * jax.nn.log_sigmoid(logits) +
    (1-y) * jax.nn.log_sigmoid(-logits)
  )).sum() / mask.sum() / y.shape[-1]  # mean

class ComputeLoss:
  def __init__(self, args: YOLOv5Args):
    self.batch_size = args.batch_size
    self.weight_decay = args.weight_decay
    self.anchors = args.anchors
    self.nc = args.num_classes
    self.coef_box = args.coef_box
    self.coef_obj = args.coef_obj
    self.coef_cls = args.coef_cls
    self.balance_obj = [4.0, 1.0, 0.4]
    self.aspect_ratio_thre = 4.0
    self.offset = jnp.array(
      [(0, 0), (-1, 0), (1, 0), (0, 1), (0, -1)], dtype=jnp.float32
    ) * 0.5

  @partial(jax.jit, static_argnums=[0,5])
  def step(
    self, state: train_state.TrainState,
    x: jnp.ndarray, box: jnp.ndarray,
    nb: jnp.ndarray, train: bool
  ):
    """
    Args:
      state: Flax TrainState
      x: Input images. [shape=(N,H,W,C)]
      box: Target boxes. [shape=(N,M,5)]
      nb: Number of boxes. [shape=(N,)]
      train: Update state if train.
    """
    def single_loss_fn(p, t, anchors):
      """
      Args:
        p (logits): [shape=(N,3,H,W,5+nc)]
        t (target): [shape=(N,3,H,W,6)]
        anchors: [shape=(3,2)]
      """
      mask = t[..., 4:5] == 1  # positive mask
      xy = (jax.nn.sigmoid(p[...,:2]) - 0.5) * 2.0 + 0.5
      wh = (jax.nn.sigmoid(p[...,2:4]) * 2) ** 2 * anchors.reshape(1,3,1,1,2)
      ious = iou(jnp.concatenate([xy, wh], -1), t[..., :4], format='ciou', keepdim=True)
      lbox = (mask * (1 - ious)).sum() / mask.sum()
      ious = jax.lax.stop_gradient(ious)  # Don't forget stop gradient after box loss
      tobj = jnp.zeros_like(ious)
      tobj += mask * jnp.clip(ious, 0.0)
      lobj = BCE(p[..., 4:5], tobj, jnp.ones_like(mask))
      hot = jax.nn.one_hot(t[..., 5], self.nc)
      lcls = BCE(p[..., 5:], hot, mask)
      return lbox, lobj, lcls
    
    def loss_fn(params):
      logits, updates = state.apply_fn(
        {'params': params, 'batch_stats': state.batch_stats},
        x, train=train, mutable=['batch_stats']  # Update (2024.1.1): train=train
      )
      targets = jax.vmap(self.build_target)(logits, box, nb)
      lbox, lobj, lcls = 0, 0, 0
      for i in range(3):
        losses = single_loss_fn(logits[i], targets[i], self.anchors[i] / (2**(i+3)))  # Update(2023.12.27): wh relative to cell
        lbox += losses[0]
        lobj += losses[1] * self.balance_obj[i]
        lcls += losses[2]
      lbox *= self.coef_box
      lobj *= self.coef_obj
      lcls *= self.coef_cls
      # weight_l2 = 0.5 * sum(jnp.sum(x**2) for x in jax.tree_util.tree_leaves(params) if x.ndim > 1)
      # loss = self.batch_size * (lbox + lobj + lcls) + self.weight_decay * weight_l2
      loss = self.batch_size * (lbox + lobj + lcls)
      return loss, (updates, lbox, lobj, lcls)
    if train:
      (loss, (updates, *metrics)), grads = jax.value_and_grad(loss_fn, has_aux=True)(state.params)
      state = accumulate_grads(state, grads)
      state = state.replace(batch_stats=updates['batch_stats'])
    else:
      loss, (_, *metrics) = loss_fn(state.params)
    return state, (loss, *metrics)
  
  @partial(jax.jit, static_argnums=0)
  def build_target(self, p: List[jnp.ndarray], box: jnp.ndarray, nb: int):
    """
    Build target for one sample.
    Args:
      p (logits): list[shape=(3,Hi,Wi,5+nc)], i=0,1,2, \
        [elem: (x,y,w,h,conf,*prob)]
      box: Target boxes with YOLO format. [shape=(M,5)]
      nb: Number of the target box.
    Return:
      target: Target for `p` cell format. \
        list[shape=(3,Hi,Wi,6)], i=0,1,2, [elem: (x,y,w,h,conf,cls)]
    """
    target = [jnp.zeros((*p[i].shape[:3],6)) for i in range(3)]
    def loop_i_fn(i, target):  # box[i]
      b, cls = box[i, :4], box[i, 4]
      rate = b[None,None,2:4] / self.anchors  # anchors.shape=(3,3,2)
      flag = jnp.maximum(rate, 1.0 / rate).max(-1) < self.aspect_ratio_thre  # shape=(3,3)

      def update_fn(value):
        t, k, c, bc = value
        t = t.at[k,c[1],c[0]].set(jnp.r_[bc, 1, cls])
        return t

      for j in range(3):  # diff scale
        s = 2 ** (j+3)
        cs = (self.offset + b[:2] / s).astype(jnp.int32)  # center in cells, shape=(5,2)
        for c in cs:  # add target to near cell
          for k in range(3):  # diff anchor in current scale
            bc = jnp.r_[b[:2]/s - c.astype(jnp.float32), b[2:4]/s]  # Update 2023.12.27: wh should relative to cell
            target[j] = jax.lax.cond(
              flag[j,k], update_fn, lambda x: x[0], (target[j], k, c, bc)
            )
      return target
    target = jax.lax.fori_loop(0, nb, loop_i_fn, target)
    return target
  
  @partial(jax.jit, static_argnums=0)
  def build_target_idxs(self, box: jnp.ndarray, nb: int):
    """
    (Deprecated) Build target with idxs for one sample.
    Args:
      box: Target boxes with YOLO format. [shape=(M,5)]
      nb: Number of the target boxes. [int]
    Return:
      idxs: The positive target boxes indexes. List[shape=(max_size, 4), elem: (batch, anchor, i, j)]
      targets: Target value for idxs. List[shape=(max_size, 6), elem: (x,y,w,h,conf,cls)]
      nt: The number of targets. List[int]
    Note:
      Since the output shape of jax must be static. max_size is the maximum of the targets.
      max_size = max_num_box * 3(anchor) * 3(offset)
    """
    pass

def cell2pixel(xy, scale):
  """
  Convert cell relative position to pixel position.
  Input shape = [B,A,H,W,C] or [H,W,C] or [B,H,W,C]
  Output shape is same as input shape.
  """
  assert xy.shape[-1] == 2
  init_shape = xy.shape
  h, w = xy.shape[-3:-1]
  if xy.ndim != 4: xy = xy.reshape(-1, h, w, 2)
  dx, dy = [jnp.repeat(x[None,...], xy.shape[0], 0) for x in jnp.meshgrid(jnp.arange(h), jnp.arange(w))]
  xy = jnp.stack([(xy[...,0]+dx)*scale, (xy[...,1]+dy)*scale], -1)
  return xy.reshape(*init_shape)

def target_debug(x, target):
  from katacv.utils.yolo.utils import show_box
  from PIL import ImageDraw, Image
  import numpy as np
  image = Image.fromarray((x*255).astype(np.uint8))
  for i in range(3):
    t = target[i]
    s = 2**(i+3)
    print(f"Scale: {s}")
    xy = cell2pixel(t[...,:2], scale=s)
    for j in range(3):
      idxs = np.transpose((t[j,...,4]).nonzero())
      print(idxs)
      # idxs = ((9,2),(10,2),(10,3))
      colors = ((255,0,0), (0,255,0), (0,0,255))
      for k, (x, y) in enumerate(idxs):
        b = jnp.concatenate([xy[j,x,y,:], t[j,x,y,2:4], t[j,x,y,5:6]], -1)[None,...]
        image = show_box(image, b, verbose=False)
        draw = ImageDraw.Draw(image)
        draw.rectangle((y*s,x*s,(y+1)*s,(x+1)*s), fill=colors[k%3])
    break
  image.show()

if __name__ == '__main__':
  from katacv.yolov5.parser import get_args_and_writer
  args = get_args_and_writer(no_writer=True)
  args.batch_size = 2
  from katacv.utils.yolo.build_dataset import DatasetBuilder
  ds_builder = DatasetBuilder(args)
  ds = ds_builder.get_dataset(subset='val')
  p = [jnp.zeros((
    args.batch_size, 3,
    args.image_shape[0]//(2**i),
    args.image_shape[1]//(2**i),
    5+80
  )) for i in range(3, 6)]
  comput_loss = ComputeLoss(args)
  for x, box, nb in ds:
    x, box, nb = x.numpy() / 255, box.numpy(), nb.numpy()
    target = jax.device_get(jax.vmap(comput_loss.build_target)(p, box, nb))
    for i in range(args.batch_size):
      i = 0
      target_debug(x[i], [t[i] for t in target])
      break
    break

