from pathlib import Path

### Dataset config ###
path_dataset = Path("/home/yy/Coding/datasets/cifar10")
batch_size = 128
shuffle_size = 128 * 16
image_shape = (56, 56, 3)
repeat = 20
use_aug = True

### Model config ###
class_num = 10
encoder_stage_size = (1, 2, 4)
decoder_stage_size = encoder_stage_size[::-1]
feature_size = 128

### Training config ###
total_epochs = 30
learning_rate = 5e-4
coef_kl_loss = 2.5e-3
coef_cls_loss = 1.0
flag_l2_image_loss = True
