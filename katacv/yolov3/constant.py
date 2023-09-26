from katacv.utils.related_pkgs.utility import *

path_dataset_tfrecord = Path("/home/yy/Coding/datasets/PASCAL/tfrecord")
# path_dataset_tfrecord = Path("/media/yy/Data/dataset/COCO/tfrecord")
# class_num = 80  # COCO
class_num = 20  # PASCAL

batch_size = 128
shuffle_size = 128 * 16
image_size = 416
split_sizes = [52, 26, 13]
anchors = [
    (0.02, 0.03), (0.04, 0.07), (0.08, 0.06),  # (10, 13), (16, 30), (33, 23),  # in 416x416
    (0.07, 0.15), (0.15, 0.11), (0.14, 0.29),  # (30, 61), (62, 45), (59, 119),
    (0.28, 0.22), (0.38, 0.48), (0.90, 0.78),  # (116, 90), (156, 198), (373, 326)
]
anchor_per = len(anchors) // len(split_sizes)
bounding_box = anchor_per
iou_ignore_threshold = 0.5

coef_noobj = 10.0
coef_coord = 10.0
coef_obj   = 1.0
coef_class = 1.0

total_epochs = 40
learning_rate = 1e-5
weight_decay = 1e-4

path_darknet = Path("/home/yy/Coding/models/YOLOv3")
darknet_id = 50