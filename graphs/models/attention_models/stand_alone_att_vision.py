import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.nn.init as init

import math


class AttentionConv(nn.Module):
    def __init__(self, in_channels, out_channels, kernel_size, stride=1, padding=[0,0,0,0], groups=1, bias=False):
        super(AttentionConv, self).__init__()
        self.out_channels = out_channels
        self.kernel_size = kernel_size
        self.stride = stride
        self.padding = padding
        self.groups = groups

        assert self.out_channels % self.groups == 0, "out_channels should be divided by groups. (example: out_channels: 40, groups: 4)"

        self.rel_h = nn.Parameter(torch.randn(out_channels // 2, 1, 1, kernel_size[0], 1), requires_grad=True)
        self.rel_w = nn.Parameter(torch.randn(out_channels // 2, 1, 1, 1, kernel_size[1]), requires_grad=True)

        self.key_conv = nn.Conv2d(in_channels, out_channels, kernel_size=1, bias=bias)
        self.query_conv = nn.Conv2d(in_channels, out_channels, kernel_size=1, bias=bias)
        self.value_conv = nn.Conv2d(in_channels, out_channels, kernel_size=1, bias=bias)

        self.reset_parameters()

    def forward(self, x):
        batch, channels, height, width = x.size()

        padded_x = F.pad(x, self.padding)
        q_out = self.query_conv(x)
        k_out = self.key_conv(padded_x)
        v_out = self.value_conv(padded_x)

        # a = nn.Unfold(kernel_size =self.kernel_size, stride = self.stride)
        k_out = k_out.unfold(2, self.kernel_size[0], self.stride).unfold(3, self.kernel_size[1],  self.stride)
        v_out = v_out.unfold(2, self.kernel_size[0], self.stride).unfold(3, self.kernel_size[1], self.stride)

        k_out_h, k_out_w = k_out.split(self.out_channels // 2, dim=1)

        k_out = torch.cat((k_out_h + self.rel_h, k_out_w + self.rel_w), dim=1)

        k_out = k_out.contiguous().view(batch, self.groups, self.out_channels // self.groups, height, width, -1)
        v_out = v_out.contiguous().view(batch, self.groups, self.out_channels // self.groups, height, width, -1)

        q_out = q_out.view(batch, self.groups, self.out_channels // self.groups, height, width, 1)

        out = q_out * k_out
        out = F.softmax(out, dim=-1)
        out = torch.einsum('bnchwk,bnchwk -> bnchw', out, v_out).view(batch, -1, height, width)

        return out

    def reset_parameters(self):
        init.kaiming_normal_(self.key_conv.weight, mode='fan_out', nonlinearity='relu')
        init.kaiming_normal_(self.value_conv.weight, mode='fan_out', nonlinearity='relu')
        init.kaiming_normal_(self.query_conv.weight, mode='fan_out', nonlinearity='relu')

        init.normal_(self.rel_h, 0, 1)
        init.normal_(self.rel_w, 0, 1)


class AttentionStem(nn.Module):
    def __init__(self, in_channels, out_channels, kernel_size, stride, padding, groups=1, m=4, bias=False):
        super(AttentionStem, self).__init__()
        self.out_channels = out_channels
        self.kernel_size = kernel_size
        self.stride = stride
        self.padding = padding
        self.groups = groups
        self.m = m

        assert self.out_channels % self.groups == 0, "out_channels should be divided by groups. (example: out_channels: 40, groups: 4)"

        self.emb_a = nn.Parameter(torch.randn(out_channels // groups, kernel_size[0], kernel_size[1]), requires_grad=True)
        self.emb_b = nn.Parameter(torch.randn(out_channels // groups, kernel_size[0], kernel_size[1]), requires_grad=True)
        self.emb_mix = nn.Parameter(torch.randn(m, out_channels // groups), requires_grad=True)

        self.key_conv = nn.Conv2d(in_channels, out_channels, kernel_size=1, bias=bias)
        self.query_conv = nn.Conv2d(in_channels, out_channels, kernel_size=1, bias=bias)
        self.value_conv = nn.ModuleList([nn.Conv2d(in_channels, out_channels, kernel_size=1, bias=bias) for _ in range(m)])

        self.reset_parameters()

    def forward(self, x):
        batch, channels, height, width = x.size()

        padded_x = F.pad(x, self.padding)

        q_out = self.query_conv(x)
        k_out = self.key_conv(padded_x)
        v_out = torch.stack([self.value_conv[_](padded_x) for _ in range(self.m)], dim=0)
        print(k_out.shape)
        k_out = k_out.unfold(2, self.kernel_size[0]* self.kernel_size[1], self.stride).unfold(3, self.kernel_size[0]* self.kernel_size[1] , self.stride)
        v_out = v_out.unfold(3, self.kernel_size[0] *self.kernel_size[1] , self.stride).unfold(4, self.kernel_size[0]* self.kernel_size[1], self.stride)
        print(k_out.shape)

        k_out = k_out[:, :, :height, :width, :, :]
        v_out = v_out[:, :, :, :height, :width, :, :]
        print(self.emb_mix.shape)
        print(self.emb_a.shape)
        emb_logit_a = torch.einsum('mc,cba->mba', self.emb_mix, self.emb_a)
        emb_logit_b = torch.einsum('mc,cba->mba', self.emb_mix, self.emb_b)
        print(emb_logit_a.shape)
        print(emb_logit_b.shape)
        emb = emb_logit_a.unsqueeze(2) + emb_logit_b.unsqueeze(1)
        print(emb.shape)
        emb = F.softmax(emb.view(self.m, -1), dim=0).view(self.m, 1, 1, 1, 1, 1, self.kernel_size[0]* self.kernel_size[1])
        print(emb.shape)
        print(v_out.shape)
        print(k_out.shape)

        v_out = emb * v_out
        k_out = k_out.contiguous().view(batch, self.groups, self.out_channels // self.groups, height, width, -1)
        v_out = v_out.contiguous().view(self.m, batch, self.groups, self.out_channels // self.groups, height, width, -1)
        v_out = torch.sum(v_out, dim=0).view(batch, self.groups, self.out_channels // self.groups, height, width, -1)

        q_out = q_out.view(batch, self.groups, self.out_channels // self.groups, height, width, 1)

        out = q_out * k_out
        out = F.softmax(out, dim=-1)
        out = torch.einsum('bnchwk,bnchwk->bnchw', out, v_out).view(batch, -1, height, width)

        return out

    def reset_parameters(self):
        init.kaiming_normal_(self.key_conv.weight, mode='fan_out', nonlinearity='relu')
        init.kaiming_normal_(self.query_conv.weight, mode='fan_out', nonlinearity='relu')
        for _ in self.value_conv:
            init.kaiming_normal_(_.weight, mode='fan_out', nonlinearity='relu')

        init.normal_(self.emb_a, 0, 1)
        init.normal_(self.emb_b, 0, 1)

class Bottleneck(nn.Module):
    expansion = 1

    def __init__(self, in_channels, out_channels, stride=1, groups=1, base_width=64):
        super(Bottleneck, self).__init__()
        self.stride = stride
        width = int(out_channels * (base_width / 64.)) * groups

        self.conv1 = nn.Sequential(
            nn.Conv2d(in_channels, width, kernel_size=1, bias=False),
            nn.BatchNorm2d(width),
            nn.ReLU(),
        )
        self.conv2 = nn.Sequential(
            AttentionConv(width, width, kernel_size=7, padding=3, groups=8),
            nn.BatchNorm2d(width),
            nn.ReLU(),
        )
        self.conv3 = nn.Sequential(
            nn.Conv2d(width, self.expansion * out_channels, kernel_size=1, bias=False),
            nn.BatchNorm2d(self.expansion * out_channels),
        )

        self.shortcut = nn.Sequential()
        if stride != 1 or in_channels != self.expansion * out_channels:
            self.shortcut = nn.Sequential(
                nn.Conv2d(in_channels, self.expansion * out_channels, kernel_size=1, stride=stride, bias=False),
                nn.BatchNorm2d(self.expansion * out_channels)
            )

    def forward(self, x):
        out = self.conv1(x)
        out = self.conv2(out)
        out = self.conv3(out)
        if self.stride >= 2:
            out = F.avg_pool2d(out, (self.stride, self.stride))

        out += self.shortcut(x)
        out = F.relu(out)

        return out

