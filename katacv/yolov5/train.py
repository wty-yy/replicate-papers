# -*- coding: utf-8 -*-
'''
@File    : train.py
@Time    : 2023/12/13 11:22:37
@Author  : wty-yy
@Version : 1.0
@Blog    : https://wty-yy.space/
@Desc    : 
'''
import sys, os
sys.path.append(os.getcwd())
from katacv.utils.related_pkgs.utility import *
from katacv.utils.related_pkgs.jax_flax_optax_orbax import *

if __name__ == '__main__':
  ### Initialize arguments and tensorboard writer ###
  from katacv.yolov5.parser import get_args_and_writer
  args, writer = get_args_and_writer()
  
  ### Initialize log manager ###
  from katacv.yolov5.logs import logs

  ### Initialize model state ###
  from katacv.yolov5.model import get_state
  state = get_state(args, use_init=not args.load_id)

  ### Load weights ###
  from katacv.utils.model_weights import load_weights
  if args.load_id > 0:
    state = load_weights(state, args)
  else:
    darknet_weights = ocp.PyTreeCheckpointer().restore(str(args.path_darknet_weights))
    state.params['CSPDarkNet_0'] = darknet_weights['params']['darknet']
    state.batch_stats['CSPDarkNet_0'] = darknet_weights['batch_stats']['darknet']
    print(f"Successfully load CSP-DarkNet53 from '{str(args.path_darknet_weights)}'")

  ### Save config ###
  from katacv.utils.model_weights import SaveWeightsManager
  save_weight = SaveWeightsManager(args, ignore_exist=True, max_to_keep=2)
  
  from katacv.utils.yolo.build_dataset import DatasetBuilder
  ds_builder = DatasetBuilder(args)
  train_ds = ds_builder.get_dataset(subset='train')
  val_ds = ds_builder.get_dataset(subset='val')

  ### Build predictor for validation ###
  from katacv.yolov5.predict import Predictor
  predictor = Predictor(args, state)

  ### Build loss updater for training ###
  from katacv.yolov5.loss import ComputeLoss
  compute_loss = ComputeLoss(args)

  ### Train and evaluate ###
  start_time, global_step = time.time(), 0
  if args.train:
    for epoch in range(state.step//len(train_ds)+1, args.total_epochs+1):
      print(f"epoch: {epoch}/{args.total_epochs}")
      print("training...")
      logs.reset()
      bar = tqdm(train_ds)
      # num_objs = []
      for x, tbox, tnum in bar:
        x, tbox, tnum = x.numpy(), tbox.numpy(), tnum.numpy()
        global_step += 1
        state, (loss, pred_pixel, other_losses) = compute_loss.train_step(state, x, tbox, tnum, train=True)
        # num_objs.append(int(num_obj))
        logs.update(
          ['loss_train', 'loss_noobj_train', 'loss_coord_train', 'loss_obj_train', 'loss_class_train'],
          [loss, *other_losses]
        )
        bar.set_description(f"loss={loss:.4f}, lr={args.learning_rate_fn(state.step):.8f}")
        if global_step % args.write_tensorboard_freq == 0:
          logs.update(
            ['SPS', 'SPS_avg', 'epoch', 'learning_rate'],
            [
              args.write_tensorboard_freq/logs.get_time_length(),
              global_step/(time.time()-start_time),
              epoch,
              args.learning_rate_fn(state.step),
            ]
          )
          logs.writer_tensorboard(writer, global_step)
          logs.reset()
      print("validating...")
      logs.reset()
      for x, tbox, tnum in tqdm(val_ds):
        x, tbox, tnum = x.numpy(), tbox.numpy(), tnum.numpy()
        predictor.update(x, tbox, tnum)
      p50, r50, ap50, ap75, map = predictor.p_r_ap50_ap75_map()
      logs.update(
        [
          'P@50_val', 'R@50_val', 'AP@50_val', 'AP@75_val', 'mAP_val',
          'epoch', 'learning_rate'
        ],
        [
          p50, r50, ap50, ap75, map,
          epoch, args.learning_rate_fn(state.step)
        ]
      )
      logs.writer_tensorboard(writer, global_step)
      predictor.reset()
      
      ### Save weights ###
      if epoch % args.save_weights_freq == 0:
        save_weight(state)
  writer.close()