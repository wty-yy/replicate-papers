# -*- coding: utf-8 -*-
'''
@File    : new_csp_darknet53.py
@Time    : 2023/12/05 21:55:04
@Author  : wty-yy
@Version : 1.0
@Blog    : https://wty-yy.space/
@Desc    : 
Struct: https://user-images.githubusercontent.com/31005897/172404576-c260dcf9-76bb-4bc8-b6a9-f2d987792583.png
Total Parameters:
Origin CSP DarkNet53 (YOLOv4): 27,656,008 (110.6 MB)
New CSP DarkNet53 (YOLOv5): 32,890,856 (131.6 MB)
2023/12/05: Start training.
2023/12/07: Complete training: 76.23%(top-1), 93.07%(top-5)
'''
import sys, os
sys.path.append(os.getcwd())

from katacv.utils.related_pkgs.utility import *
from katacv.utils.related_pkgs.jax_flax_optax_orbax import *

class ConvBlock(nn.Module):
  filters: int
  norm: nn.Module
  act: Callable
  kernel: Tuple[int, int] = (1, 1)
  strides: Tuple[int, int] = (1, 1)
  padding: str | Tuple[int, int] = 'SAME'
  use_norm: bool = True
  use_act: bool = True

  @nn.compact
  def __call__(self, x):
    x = nn.Conv(self.filters, self.kernel, self.strides, self.padding, use_bias=not self.use_norm)(x)
    if self.use_norm: x = self.norm()(x)
    if self.use_act: x = self.act(x)
    return x

class BottleNeck(nn.Module):
  conv: nn.Module
  shortcut: bool

  @nn.compact
  def __call__(self, x):
    residue = x
    c = x.shape[-1]
    x = self.conv(filters=c, kernel=(1,1))(x)
    x = self.conv(filters=c, kernel=(3,3))(x)
    if self.shortcut:
      x = x + residue
    return x

class CSP(nn.Module):
  n_bottleneck: int
  conv: nn.Module
  output_channel: int
  shortcut: bool = True

  @nn.compact
  def __call__(self, x):
    neck = partial(BottleNeck, conv=self.conv, shortcut=self.shortcut)
    n = x.shape[-1] // 2
    route = self.conv(filters=n, kernel=(1,1))(x)
    x = self.conv(filters=n, kernel=(1,1))(x)
    for _ in range(self.n_bottleneck):
      x = neck()(x)
    x = jnp.concatenate([x, route], axis=-1)
    return self.conv(filters=self.output_channel, kernel=(1,1))(x)

class CSPDarkNet(nn.Module):
  act: Callable = nn.silu

  @nn.compact
  def __call__(self, x, train: bool):
    stage_size = [3, 6, 9, 6]
    norm = partial(nn.BatchNorm, use_running_average=not train)
    conv = partial(ConvBlock, norm=norm, act=self.act)
    csp = partial(CSP, conv=conv)
    x = conv(filters=64, kernel=(6,6), strides=(2,2), padding=(2,2))(x)  # P1
    outputs = []  # P3, P4, P5
    for i, n_blockneck in enumerate(stage_size):  # start from P2
      x = conv(filters=x.shape[-1]*2, kernel=(3,3), strides=(2,2))(x)
      x = csp(n_bottleneck=n_blockneck, output_channel=x.shape[-1])(x)
      if i >= 1: outputs.append(x)
    return outputs

class PreTrain(nn.Module):
  darknet: nn.Module

  @nn.compact
  def __call__(self, x, train: bool):
    x = self.darknet(x, train)[-1]
    x = jnp.mean(x, (1, 2))
    x = nn.Dense(1000)(x)
    return x

if __name__ == '__main__':
  from katacv.utils.imagenet.train import train
  model = PreTrain(darknet=CSPDarkNet())
  train(model, "NewCSPDarkNet53", verbose=True)
