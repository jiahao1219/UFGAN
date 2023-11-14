import math
from pathlib import Path
import torch
import numpy as np
import argparse
from torch import nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, Dataset
from torchvision import transforms
import cv2
import h5py
import tqdm
from model import U_GAN
from model import Discriminator
from logger import getLogger


class imgDataset(Dataset):
    global args
    global mylogger

    def __init__(self, is_train=True, transform=True, path="./Train_ir"):
        super(imgDataset, self).__init__()
        self.checkpoint_path = None
        self.is_train = is_train
        self.transform = transform
        Path(args.checkpoint_dir / path).mkdir(exist_ok=True)
        if self.transform:
            self.trans = transforms.Compose([transforms.ToTensor(), transforms.Normalize([0.5], [0.5])])
        if self.is_train:
            self.checkpoint_path = str(Path(args.checkpoint_dir) / path / "train.h5")
            if Path(self.checkpoint_path).exists():
                mylogger.info(f"训练|已经存在训练集的h5文件,直接读取")
            else:
                self.patch_img(path)

            with h5py.File(self.checkpoint_path, 'r') as hf:
                self.img = np.array(hf.get('data'))
                self.label = np.array(hf.get('label'))
        else:
            self.img_path = path
            self.checkpoint_path = Path(args.checkpoint_dir) / path / "test.h5"
            if Path(self.checkpoint_path).exists():
                mylogger.info(f"测试|已经存在测试集的h5文件,直接读取")
            else:
                mylogger.info(f"测试|制作测试集")
                total_img = list(Path(path).glob("*.bmp"))
                total_img.extend(Path(path).glob("*.jpg"))
                total_img.extend(Path(path).glob("*.png"))
                total_img.extend(Path(path).glob("*.tif"))
                total_img.sort(key=lambda x: int(x.stem))
                sub_img = []
                sub_label = []
                for index in range(len(total_img)):
                    label = cv2.imread(str(total_img[index]), cv2.IMREAD_GRAYSCALE)
                    padding = args.patch_size - args.label_size
                    # 将源图像做填充
                    img = F.pad(label,
                                (padding // 2, padding - padding // 2, padding // 2, padding - padding // 2),
                                'constant', 127)
                    [h, w] = img.shape
                    img = img.reshape([h, w, 1])
                    label = label.reshpae([label.shape[0], label.shape[1], 1])

                    sub_img.append(img)
                    sub_label.append(label)
                sub_img = np.array(sub_img)
                sub_label = np.array(sub_label)
                with h5py.File(self.checkpoint_path, "w") as hf:
                    hf.create_dataset('data', data=sub_img)
                    hf.create_dataset('label', data=sub_label)

            with h5py.File(self.checkpoint_path, 'r') as hf:
                self.img = np.array(hf.get('data'))
                self.label = np.array(hf.get('label'))

    def __len__(self):
        return len(self.img)

    def __getitem__(self, idx):
        img = self.img[idx]
        label = self.label[idx]
        if self.transform:
            img = self.trans(img)
            label = self.trans(label)
        return img, label

    def patch_img(self, img_path):
        mylogger.info(f"训练|开始切分训练集")
        total_img = list(Path(img_path).glob("*.bmp"))
        total_img.extend(list(Path(img_path).glob("*.tif")))
        total_img.extend(list(Path(img_path).glob("*.jpg")))
        total_img.extend(list(Path(img_path).glob("*.png")))

        total_img.sort(key=lambda x: int(x.stem))
        self._patch(total_img, img_path)

    def _patch(self, total_img, path):
        sub_img = []
        sub_label = []
        padding = (args.patch_size - args.label_size) // 2
        for index in range(len(total_img)):
            [h, w] = cv2.imread(str(total_img[index]), cv2.IMREAD_GRAYSCALE).shape
            for x in range(0, h - args.patch_size, args.stride_size):
                for y in range(0, w - args.patch_size, args.stride_size):
                    patch_img = cv2.imread(str(total_img[index]), cv2.IMREAD_GRAYSCALE)  # 读取整张图像
                    label = patch_img[x + padding:x + padding + args.label_size,
                            y + padding:y + padding + args.label_size]
                    label = label.reshape([args.label_size, args.label_size, 1])
                    patch = patch_img[x:x + args.patch_size, y:y + args.patch_size]
                    patch = patch.reshape([args.patch_size, args.patch_size, 1])
                    sub_img.append(patch)
                    sub_label.append(label)
        sub_img = np.asarray(sub_img)
        sub_label = np.asarray(sub_label)
        with h5py.File(self.checkpoint_path, "w") as hf:
            hf.create_dataset('data', data=sub_img)
            hf.create_dataset('label', data=sub_label)


def gradient(input):
    d = F.conv2d(input, torch.tensor([[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]], device=device).unsqueeze(0).unsqueeze(
        0).float())  # sobel算子
    return d


def train(G, D, ir_dataloader, vi_dataloader):
    if args.is_train:
        G_optimizer = torch.optim.Adam(G.parameters())
        D_optimizer = torch.optim.Adam(D.parameters())
        for epoch in range(args.epochs):
            mylogger.info(f"训练|开始训练第{epoch + 1}个epoch,一次epoch包含{len(ir_dataloader)}个batch")
            for batch, ((ir_img, ir_label), (vi_img, vi_label)) in enumerate(zip(ir_dataloader, vi_dataloader)):
                ir_img = ir_img.to(device)
                ir_label = ir_label.to(device)
                vi_img = vi_img.to(device)
                vi_label = vi_label.to(device)
                input_img = torch.cat([ir_img, vi_img], dim=1)
                G.eval()
                D.train()
                G_out = G(input_img)
                D_out = D(G_out)
                pos = D(vi_label)
                batch_size = D_out.shape[0]
                # 辨别器损失
                D_loss = torch.mean(
                    torch.square(D_out - torch.rand([batch_size, 1], device=device) * 0.3)) + torch.mean(
                    torch.square(pos - torch.rand([batch_size, 1], device=device) * 0.5 + 0.7))
                D_loss.backward()
                D_optimizer.step()
                D_optimizer.zero_grad()
                if (batch + 1) % args.generator_interval == 0:
                    G.train()
                    D.eval()
                    G_out = G(input_img)
                    D_out = D(G_out)
                    G_content_loss = torch.mean(
                        torch.square(G_out - ir_label)) + 5 * torch.mean(
                        torch.square(gradient(G_out) - gradient(vi_label)))
                    G_adversarial_loss = torch.mean(
                        torch.square(D_out - torch.rand([batch_size, 1], device=device) * 0.5 + 0.7))
                    # 生成器损失
                    G_loss = G_adversarial_loss + 100 * G_content_loss
                    G_loss.backward()
                    G_optimizer.step()
                    G_optimizer.zero_grad()
                else:
                    continue
            if (epoch + 1) % args.log_interval == 0:
                mylogger.info(f"训练|第{epoch + 1}个epoch|G_loss:{G_loss:>5f}|D_loss:{D_loss:>5f}")
                torch.save(G.state_dict(), f"{args.checkpoint_dir}/G_{epoch + 1}.pth")
                torch.save(D.state_dict(), f"{args.checkpoint_dir}/D_{epoch + 1}.pth")
    else:
        D.eval()
        G.eval()
        with torch.inference_mode():
            for epoch in args.epochs:
                for batch, (ir_img, ir_label, (vi_img, vi_label),) in enumerate(zip(ir_dataloader, vi_dataloader)):
                    mylogger.info(f"测试|开始训练第{epoch + 1}个epoch")
                    input_img = torch.cat([ir_img, vi_img], dim=1)
                    G_out = G(input_img)
                    D_out = D(G_out)
                    pos = D(vi_label)
                    D_loss = torch.mean(torch.square(D_out - torch.rand([args.batch_size, 1]) * 0.3)) + torch.mean(
                        torch.square(pos - torch.rand([args.batch_size, 1]) * 0.5 + 0.7))
                    G_content_loss = torch.mean(
                        torch.square(G_out - ir_label) + 5 * torch.square(gradient(G_out) - gradient(vi_label)))
                    G_adversarial_loss = torch.mean(torch.square(D_out - torch.rand([args.batch_size, 1]) * 0.5 + 0.7))
                    G_loss = G_adversarial_loss + 100 * G_content_loss
                if (epoch + 1) % args.log_interval == 0:
                    mylogger.info(f"测试|第{epoch + 1}个epoch|G_loss:{G_loss:>5f}|D_loss:{D_loss:>5f}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='FusionGAN for pytorch.')
    parser.add_argument("--is_train", "-t", type=bool, default=True)
    parser.add_argument("--batch_size", "-b", type=int, default=32)
    parser.add_argument("--patch_size", "-p", type=int, default=160)
    parser.add_argument("--label_size", "-l", type=int, default=152)
    parser.add_argument("--stride_size", "-s", type=int, default=60)
    parser.add_argument("--epochs", "-e", type=int, default=30)
    parser.add_argument("--checkpoint_dir", "-c", type=str, default="./checkpoint")
    parser.add_argument("--log_dir", "-ld", type=str, default="./log")
    parser.add_argument("--log_interval", "-li", type=int, default=5)
    parser.add_argument("--generator_interval", "-gi", type=int, default=2, help="interval between update G")
    args = parser.parse_args()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    mylogger = getLogger("FusionGAN", log_dir=args.log_dir)
    ir_dataset = imgDataset(path="./Train_ir")
    vi_dataset = imgDataset(path="./Train_vi")
    ir_dataloader = DataLoader(ir_dataset, batch_size=args.batch_size, shuffle=True)
    vi_dataloader = DataLoader(vi_dataset, batch_size=args.batch_size, shuffle=True)
    assert len(ir_dataloader) == len(vi_dataloader), "红外图像和可见光图像数量不一致"
    G = U_GAN().to(device)
    D = Discriminator().to(device)
    train(G, D, ir_dataloader, vi_dataloader)
