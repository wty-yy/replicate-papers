# -*- coding: utf-8 -*-
'''
@File    : crnn_model_bilstm.py
@Time    : 2023/10/16 21:48:35
@Author  : wty-yy
@Version : 1.0
@Blog    : https://wty-yy.space/
@Desc    : 
Use BiLSTM, VGG struct. Base on CRNN paper: https://arxiv.org/pdf/1507.05717.pdf
'''
from katacv.utils.related_pkgs.jax_flax_optax_orbax import *
from katacv.utils.related_pkgs.utility import *

def mish(x):
    return x * jnp.tanh(jax.nn.softplus(x))

class ConvBlock(nn.Module):
    filters: int
    norm: nn.Module
    act: Callable
    kernel: Sequence[int] = (1, 1)
    strides: Sequence[int] = (1, 1)
    padding: str | Sequence[Sequence[int]] = 'SAME'
    use_norm: bool = True
    use_act: bool = True

    @nn.compact
    def __call__(self, x):
        x = nn.Conv(self.filters, self.kernel, self.strides, self.padding, use_bias=not self.use_norm)(x)
        if self.use_norm: x = self.norm()(x)
        if self.use_act: x = self.act(x)
        return x

class OCR_CRNN(nn.Module):
    class_num: int
    stage_size = [1, 2, 2]
    act: Callable = mish
    bilstm_features: int = 256

    @nn.compact
    def __call__(self, x, train:bool=True):
        norm = partial(nn.BatchNorm, use_running_average=not train)
        conv = partial(ConvBlock, norm=norm, act=self.act)
        lstm = lambda: nn.RNN(nn.OptimizedLSTMCell(self.bilstm_features))
        bilstm = lambda: nn.Bidirectional(forward_rnn=lstm(), backward_rnn=lstm())
        x = x / 255.0
        x = conv(filters=64, kernel=(3,3))(x)
        for i, block_num in enumerate(self.stage_size):
            strides = (2, 2) if i < 2 else (2, 1)
            x = nn.max_pool(x, strides, strides)
            n = x.shape[-1]
            for _ in range(block_num):
                x = conv(filters=n*2, kernel=(3,3))(x)
        x = nn.max_pool(x, (2,1), (2,1))
        x = conv(filters=x.shape[-1], kernel=(2,2), padding=((0,0),(0,0)))(x)  # (B,1,24,512)
        x = x[:,0,:,:]  # (B,T,features)
        x = nn.Dense(64)(x)
        x = bilstm()(x)
        x = bilstm()(x)
        x = nn.Dense(self.class_num)(x)
        return x  # (B,W//4-1,class_num)

class TrainState(train_state.TrainState):
    batch_stats: dict

from katacv.ocr.parser import OCRArgs
def get_ocr_crnn_bilstm_state(args: OCRArgs, verbose=False) -> TrainState:
    model = OCR_CRNN(class_num=args.class_num)
    key = jax.random.PRNGKey(args.seed)
    if verbose: print(model.tabulate(key, jnp.empty(args.input_shape), train=False))
    variables = model.init(key, jnp.empty(args.input_shape), train=False)
    return TrainState.create(
        apply_fn=model.apply,
        params=variables['params'],
        tx=optax.adam(learning_rate=args.learning_rate),
        batch_stats=variables['batch_stats']
    )

if __name__ == '__main__':
    from katacv.ocr.parser import get_args_and_writer
    args = get_args_and_writer(no_writer=True)
    state = get_ocr_crnn_bilstm_state(args, verbose=True)
    x = jnp.empty(args.input_shape)
    logits, updates = state.apply_fn(
        {'params': state.params, 'batch_stats': state.batch_stats},
         x, train=False, mutable=['batch_stats']
    )
    print(logits.shape)
