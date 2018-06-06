_author__ = 'MSteger'

import numpy as np
import torch
import PIL
import os, gc
import scipy.ndimage as nd
import PIL.Image
from torch.autograd import Variable
from torchvision import transforms, models

class DreamPhant(object):

    def __init__(self, model, input_dir, device = torch.device('cpu'), step_fn=None, verbose = True):
        self.model = model.to(device)
        self.input_dir = input_dir
        self.device = device
        self.step_fn = self.make_step if step_fn is None else step_fn
        self.verbose = verbose

    def _load_image(self, path, preprocess, resize):
        img = PIL.Image.open(path)
        if resize is not None: img.thumbnail(resize, PIL.Image.ANTIALIAS)
        img_tensor = preprocess(img).unsqueeze(0) if preprocess is not None else transforms.ToTensor(img)
        return img, img_tensor, img_tensor.numpy()

    def _tensor_to_img(self, t):
        a = t.numpy()
        mean = np.array([0.485, 0.456, 0.406]).reshape([1, 1, 3])
        std = np.array([0.229, 0.224, 0.225]).reshape([1, 1, 3])
        inp = a[0, :, :, :]
        inp = inp.transpose(1, 2, 0)
        inp = std * inp + mean
        inp *= 255
        inp = np.uint8(np.clip(inp, 0, 255))
        return PIL.Image.fromarray(inp)

    def _image_to_variable(self, image, requires_grad=False):
        return Variable(image.cuda() if self.device == torch.device('cuda') else image, requires_grad=requires_grad)

    def _extract_features(self, img_tensor, layer):
        features = self._image_to_variable(img_tensor, requires_grad=True) if not isinstance(img_tensor, (torch.cuda.FloatTensor if self.device == torch.device('cuda')  else torch.Tensor)) else img_tensor
        for index, current_layer in enumerate(self.model.features.children()):
            features = current_layer(features)
            if index == layer: break
        return features

    def objective(self, dst, guide_features=None):
        if guide_features is None:
            return dst.data
        else:
            x = dst.data[0].cpu().numpy()
            y = guide_features.data[0].cpu().numpy()
            ch, w, h = x.shape
            x = x.reshape(ch, -1)
            y = y.reshape(ch, -1)
            A = x.T.dot(y)
            diff = y[:, A.argmax(1)]
            return torch.Tensor(np.array([diff.reshape(ch, w, h)])).to(self.device)

    def make_step(self, img, control=None, step_size=1.5, layer=28, jitter=32):

        mean = np.array([0.485, 0.456, 0.406]).reshape([3, 1, 1])
        std = np.array([0.229, 0.224, 0.225]).reshape([3, 1, 1])

        ox, oy = np.random.randint(-jitter, jitter + 1, 2)

        img = np.roll(np.roll(img, ox, -1), oy, -2)
        tensor = torch.Tensor(img)

        img_var = self._image_to_variable(tensor, requires_grad=True)
        self.model.zero_grad()

        x = self._extract_features(img_tensor=img_var, layer=layer)
        delta = self.objective(x, control)
        x.backward(delta)

        # L2 Regularization on gradients
        mean_square = torch.Tensor([torch.mean(img_var.grad.data ** 2)]).to(self.device)
        img_var.grad.data /= torch.sqrt(mean_square)
        img_var.data.add_(img_var.grad.data * step_size)

        result = img_var.data.cpu().numpy()
        result = np.roll(np.roll(result, -ox, -1), -oy, -2)
        result[0, :, :, :] = np.clip(result[0, :, :, :], -mean / std, (1 - mean) / std)

        return torch.Tensor(result)

    def DeepDream(self, base_img, octave_n=6, octave_scale=1.4, iter_n=10, **step_args):
        octaves = [base_img]
        for i in range(octave_n - 1): octaves.append(nd.zoom(octaves[-1], (1, 1, 1.0 / octave_scale, 1.0 / octave_scale), order=1))

        detail = np.zeros_like(octaves[-1])

        for octave, octave_base in enumerate(octaves[::-1]):
            h, w = octave_base.shape[-2:]
            if octave > 0:
                h1, w1 = detail.shape[-2:]
                detail = nd.zoom(detail, (1, 1, 1.0 * h / h1, 1.0 * w / w1), order=1)
            src = octave_base + detail
            for i in range(iter_n):
                src = self.step_fn(src, **step_args)

            detail = src.numpy() - octave_base

        return src

    def transform(self, preprocess, layer, resize = [1024, 1024], **dream_args):
        for img_name in os.listdir(self.input_dir):
            img_path = os.path.join(self.input_dir, img_name)
            input_img, input_tensor, input_np = self._load_image(path=img_path, preprocess=preprocess, resize=resize)
            DeepDream = self.DeepDream(base_img=input_np, layer = layer, **dream_args)
            DeepDream = self._tensor_to_img(DeepDream)
            output_dir = os.path.join(self.input_dir.replace('/input', '/output'), 'layer{}'.format(layer))
            if not os.path.exists(output_dir): os.makedirs(output_dir)
            output_path = os.path.join(output_dir, img_name)
            DeepDream.save(output_path)
            if self.verbose: print 'saved img {} to {}'.format(os.path.split(img_name)[-1], output_path)
        return self

if __name__ == '__main__':
    from models.PhantNet import PhantNet
    from components.summary import summary

    # setup
    input_dir = r'/media/msteger/storage/resources/DreamPhant/dream/input/'
    guideImage = r'/media/msteger/storage/resources/DreamPhant/dream/input/single_african_phant.jpg'
    model_chkp = r'/media/msteger/storage/resources/DreamPhant/models/run/2018-06-05 20:35:22.740193__0.359831720591__449.pkl'
    preprocess = transforms.Compose([transforms.ToTensor(), transforms.Normalize(mean=[0.485, 0.456, 0.406],std=[0.229, 0.224, 0.225])])
    device = torch.device('cuda')

    # model
    model = PhantNet(pretrained_models=models.alexnet(pretrained=True), input_shape=(3, 224, 224), freeze_feature_layers=-1, freeze_classifier_layers=-1, replace_classifier=True, num_class=2)
    chkp_dict = torch.load(model_chkp)
    model.load_state_dict(chkp_dict['state_dict'])
    summary(model=model, device=device, input_size=(1,) + model.input_shape)

    # dreaming
    for layer in range(0, 17):
        Dream = DreamPhant(model=model, input_dir=input_dir, device=device)
        Dream.transform(preprocess = preprocess, resize = [1920, 1080], layer = layer, octave_n=6, octave_scale=1.4,iter_n=25, control=None, step_size=0.01, jitter=32)
        Dream = None
        gc.collect()

    print 'done'