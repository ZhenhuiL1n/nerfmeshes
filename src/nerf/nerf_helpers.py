import torch
import numpy as np
import torchvision

from typing import Optional

POINT_GROUND_TRUTH = torch.tensor([ 0., 0., 255. ])
POINT_OUT_TRUE = torch.tensor([ 0., 255., 0. ])
POINT_OUT_FALSE_VOID = torch.tensor([ 0., 0., 0. ])
POINT_OUT_FALSE_SURFACE = torch.tensor([ 255., 0., 0. ])


def img2mse(img_src, img_tgt):
    return torch.nn.functional.mse_loss(img_src, img_tgt)


def mse2psnr(mse):
    # For numerical stability, avoid a zero mse loss.
    if mse == 0:
        mse = 1e-5

    # MAX(i) is 1.0
    return -10.0 * torch.log10(mse)


def get_point_clouds(ray_origins, ray_directions, depth_output, depth_target = None, threshold = 0.2, empty = 0.):
    # Point cloud to be returned
    if depth_target is not None:
        # Target point cloud
        target_points = create_point_cloud(ray_origins, ray_directions, depth_target, POINT_GROUND_TRUTH)

        # Compute residuals for inv accuracy (FP + FN) mask
        mask_true = torch.abs(depth_output - depth_target) < threshold

        # Extract only positive samples
        out_points = create_point_cloud(ray_origins, ray_directions, depth_output, POINT_OUT_TRUE, mask_true)

        # Surface mask
        mask_surface = (depth_target != empty) & ~mask_true
        mask_empty = (depth_target == empty) & ~mask_true

        # Extract only negative samples representing void
        out_points_empty = create_point_cloud(ray_origins, ray_directions, depth_output, POINT_OUT_FALSE_VOID, mask_empty)
        out_points_surface = create_point_cloud(ray_origins, ray_directions, depth_output, POINT_OUT_FALSE_SURFACE, mask_surface)

        # Bundle them together
        data = list(zip(target_points, out_points, out_points_empty, out_points_surface))
        point_cloud = [ torch.cat(type, dim = 0) for type in data ]
    else:
        # Output point cloud
        point_cloud = create_point_cloud(ray_origins, ray_directions, depth_output, POINT_GROUND_TRUTH)

    return point_cloud


def create_point_cloud(ray_origins, ray_directions, depth, color, mask = None):
    if mask is not None:
        ray_directions, depth = ray_directions[mask], depth[mask]

    vertices = (ray_origins + ray_directions * depth[..., None]).view(-1, 3)
    diffuse = color.expand(vertices.shape)
    normals = -ray_directions.view(-1, 3)

    return vertices, diffuse, normals


def comp_depth(depth_output, depth_target, empty_value = 0.):
    mask = depth_target > empty_value
    depth_loss = torch.nn.functional.mse_loss(
        depth_output, depth_target
    )

    depth_empty = torch.nn.functional.mse_loss(
        depth_output[~mask], depth_target[~mask]
    )

    depth_space = torch.nn.functional.mse_loss(
        depth_output[mask], depth_target[mask]
    )

    depth_l1 = (depth_output[mask] - depth_target[mask]).mean()

    return depth_loss, depth_empty, depth_space, depth_l1


def export_obj(vertices, triangles, diffuse, normals, filename):
    """
    Exports a mesh in the (.obj) format.
    """
    print('Writing to obj...')

    with open(filename, "w") as fh:

        for index, v in enumerate(vertices):
            fh.write("v {} {} {}".format(*v))
            if len(diffuse) > index:
                fh.write(" {} {} {}".format(*diffuse[index]))

            fh.write("\n")

        for n in normals:
            fh.write("vn {} {} {}\n".format(*n))

        for f in triangles:
            fh.write("f")
            for index in f:
                fh.write(" {}//{}".format(index + 1, index + 1))

            fh.write("\n")

    print('Finished writing to obj')


def export_point_cloud(it, ray_origins, ray_directions, depth_fine, dep_target):
    vertices_output = (ray_origins + ray_directions * depth_fine[..., None]).view(-1, 3)
    vertices_target = (ray_origins + ray_directions * dep_target[..., None]).view(-1, 3)
    vertices = torch.cat((vertices_output, vertices_target), dim = 0)
    diffuse_output = torch.zeros_like(vertices_output)
    diffuse_output[:, 0] = 1.0
    diffuse_target = torch.zeros_like(vertices_target)
    diffuse_target[:, 2] = 1.0
    diffuse = torch.cat((diffuse_output, diffuse_target), dim = 0)
    normals = torch.cat((-ray_directions.view(-1, 3), -ray_directions.view(-1, 3)), dim = 0)
    export_obj(vertices, [], diffuse, normals, f"{it:04d}.obj")


def cast_to_image(tensor):
    # Input tensor is (H, W, 3). Convert to (3, H, W).
    tensor = tensor.permute(2, 0, 1)
    # Conver to PIL Image and then np.array (output shape: (H, W, 3))
    img = np.array(torchvision.transforms.ToPILImage()(tensor.detach().cpu().float()))
    # Map back to shape (3, H, W), as tensorboard needs channels first.
    img = np.moveaxis(img, [-1], [0])
    return img


def cast_to_disparity_image(tensor):
    # Input tensor is (H, W). Convert to (1, H, W).
    img = (tensor - tensor.min()) / (tensor.max() - tensor.min())
    img = img.clamp(0, 1) * 255
    img = img.unsqueeze(0).numpy().astype(np.uint8)
    img = img.detach().cpu()
    return img


def get_minibatches(inputs: torch.Tensor, chunksize: Optional[int] = 1024 * 8):
    r"""Takes a huge tensor (ray "bundle") and splits it into a list of minibatches.
    Each element of the list (except possibly the last) has dimension `0` of length
    `chunksize`.
    """
    return [inputs[i: i + chunksize] for i in range(0, inputs.shape[0], chunksize)]


def meshgrid_xy(
        tensor1: torch.Tensor, tensor2: torch.Tensor
) -> (torch.Tensor, torch.Tensor):
    """Mimick np.meshgrid(..., indexing="xy") in pytorch. torch.meshgrid only allows "ij" indexing.
    (If you're unsure what this means, safely skip trying to understand this, and run a tiny example!)

    Args:
      tensor1 (torch.Tensor): Tensor whose elements define the first dimension of the returned meshgrid.
      tensor2 (torch.Tensor): Tensor whose elements define the second dimension of the returned meshgrid.
    """
    # TESTED
    ii, jj = torch.meshgrid(tensor1, tensor2)
    return ii.transpose(-1, -2), jj.transpose(-1, -2)

index = 0


def cumprod_exclusive(tensor: torch.Tensor) -> torch.Tensor:
    r"""Mimick functionality of tf.math.cumprod(..., exclusive=True), as it isn't available in PyTorch.

    Args:
    tensor (torch.Tensor): Tensor whose cumprod (cumulative product, see `torch.cumprod`) along dim=-1
      is to be computed.

    Returns:
    cumprod (torch.Tensor): cumprod of Tensor along dim=-1, mimiciking the functionality of
      tf.math.cumprod(..., exclusive=True) (see `tf.math.cumprod` for details).
    """
    # TESTED
    # Only works for the last dimension (dim=-1)
    dim = -1

    # Compute regular cumprod first (this is equivalent to `tf.math.cumprod(..., exclusive=False)`).
    cumprod = torch.cumprod(tensor, dim)

    # "Roll" the elements along dimension 'dim' by 1 element.
    cumprod = torch.roll(cumprod, 1, dim)

    # Replace the first element by "1" as this is what tf.cumprod(..., exclusive=True) does.
    cumprod[..., 0] = 1.0

    return cumprod


def get_ray_bundle(
        height: int,
        width: int,
        focal_length: float,
        tform_cam2world: torch.Tensor
):
    """ Compute the bundle of rays passing through all pixels of an image (one ray per pixel).
    Args:
        height (int): Height of an image (number of pixels).
        width (int): Width of an image (number of pixels).
        focal_length (float or torch.Tensor): Focal length (number of pixels, i.e., calibrated intrinsics).
        tform_cam2world (torch.Tensor): A 6-DoF rigid-body transform (shape: :math:`(4, 4)`) that
          transforms a 3D point from the camera frame to the "world" frame for the current example.
    Returns:
        ray_origins (torch.Tensor): A tensor of shape :math:`(width, height, 3)` denoting the centers of
          each ray. `ray_origins[i][j]` denotes the origin of the ray passing through pixel at
          row index `j` and column index `i`.
        ray_directions (torch.Tensor): A tensor of shape :math:`(width, height, 3)` denoting the
          direction of each ray (a unit vector). `ray_directions[i][j]` denotes the direction of the ray
          passing through the pixel at row index `j` and column index `i`.
    """
    ii, jj = meshgrid_xy(
        torch.arange(
            width, dtype = tform_cam2world.dtype, device = tform_cam2world.device
        ).to(tform_cam2world),
        torch.arange(
            height, dtype = tform_cam2world.dtype, device = tform_cam2world.device
        ),
    )

    # Directions shape (W, H, 3)
    directions = torch.stack(
        [
            (ii - width * 0.5) / focal_length,
            -(jj - height * 0.5) / focal_length,
            -torch.ones_like(ii),
        ],
        dim = -1,
    )

    # Normalized rays, spherical / pinhole camera
    directions_norm = directions / directions.norm(2, dim = -1)[..., None]

    # Ray directions (W, H, 1, 3) @ (3, 3) => (W, H, 3, 3) => (W, H, 3)
    ray_directions = torch.sum(
        directions_norm[..., None, :] * tform_cam2world[:3, :3], dim = -1
    )

    # Ray origins (3,) => (1, 3)
    ray_origins = tform_cam2world[:3, -1]

    return ray_origins, ray_directions


def positional_encoding(
        tensor, num_encoding_functions = 6,
        include_input = True,
        log_sampling = True
) -> torch.Tensor:
    r"""Apply positional encoding to the input.

    Args:
        tensor (torch.Tensor): Input tensor to be positionally encoded.
        encoding_size (optional, int): Number of encoding functions used to compute
            a positional encoding (default: 6).
        include_input (optional, bool): Whether or not to include the input in the
            positional encoding (default: True).

    Returns:
    (torch.Tensor): Positional encoding of the input tensor.
    """
    # TESTED
    # Trivially, the input tensor is added to the positional encoding.
    encoding = [tensor] if include_input else []
    frequency_bands = None
    if log_sampling:
        frequency_bands = 2.0 ** torch.linspace(
            0.0,
            num_encoding_functions - 1,
            num_encoding_functions,
            dtype = tensor.dtype,
            device = tensor.device,
        )
    else:
        frequency_bands = torch.linspace(
            2.0 ** 0.0,
            2.0 ** (num_encoding_functions - 1),
            num_encoding_functions,
            dtype = tensor.dtype,
            device = tensor.device,
        )

    for freq in frequency_bands:
        for func in [torch.sin, torch.cos]:
            encoding.append(func(tensor * freq))

    # Special case, for no positional encoding
    if len(encoding) == 1:
        return encoding[0]
    else:
        return torch.cat(encoding, dim = -1)


def get_embedding_function(
        num_encoding_functions = 6, include_input = True, log_sampling = True
):
    r"""Returns a lambda function that internally calls positional_encoding.
    """
    return lambda x: positional_encoding(
        x, num_encoding_functions, include_input, log_sampling
    )


def ndc_rays(H, W, focal, near, rays_o, rays_d):
    # UNTESTED, but fairly sure.

    # Shift rays origins to near plane
    t = -(near + rays_o[..., 2]) / rays_d[..., 2]
    rays_o = rays_o + t[..., None] * rays_d

    # Projection
    o0 = -1.0 / (W / (2.0 * focal)) * rays_o[..., 0] / rays_o[..., 2]
    o1 = -1.0 / (H / (2.0 * focal)) * rays_o[..., 1] / rays_o[..., 2]
    o2 = 1.0 + 2.0 * near / rays_o[..., 2]

    d0 = (
            -1.0
            / (W / (2.0 * focal))
            * (rays_d[..., 0] / rays_d[..., 2] - rays_o[..., 0] / rays_o[..., 2])
    )
    d1 = (
            -1.0
            / (H / (2.0 * focal))
            * (rays_d[..., 1] / rays_d[..., 2] - rays_o[..., 1] / rays_o[..., 2])
    )
    d2 = -2.0 * near / rays_o[..., 2]

    rays_o = torch.stack([o0, o1, o2], -1)
    rays_d = torch.stack([d0, d1, d2], -1)

    return rays_o, rays_d


if __name__ == "__main__":
    # # meshgrid_xy
    # i, j = np.meshgrid(np.arange(3), np.arange(4, 7), indexing='xy')
    # print(i)
    # print(j)
    # ii, jj = torch.meshgrid(torch.arange(3), torch.arange(4, 7))
    # print(ii.transpose(-1, -2))
    # print(jj.transpose(-1, -2))
    # ii, jj = meshgrid_xy(torch.arange(3), torch.arange(4, 7))
    # print(ii)
    # print(jj)

    # # dirs (get_rays_np)
    # H, W = 3, 3
    # focal = 10
    # i, j = np.meshgrid(np.arange(3), np.arange(4, 7), indexing='xy')
    # dirs = np.stack([(i - W) * .5 / focal, -(j - H) * .5 / focal, -np.ones_like(i)], -1)
    # print(dirs)
    # ii, jj = meshgrid_xy(torch.arange(3).float(), torch.arange(4, 7).float())
    # dirs_torch = torch.stack([(ii - W) * .5 / focal, -(jj - H) * .5 / focal, -torch.ones_like(ii)], -1)
    # print(dirs_torch)
    # print(np.allclose(dirs, dirs_torch.cpu().numpy()))

    # # rays_o, rays_d (get_rays_np)
    # H, W = 3, 3
    # focal = 10
    # c2w = np.eye(4)
    # c2w[:3, :3] = 2 * c2w[:3, :3]
    # i, j = np.meshgrid(np.arange(3), np.arange(4, 7), indexing='xy')
    # dirs = np.stack([(i - W) * .5 / focal, -(j - H) * .5 / focal, -np.ones_like(i)], -1)
    # rays_d = np.sum(dirs[..., np.newaxis, :] * c2w[:3, :3], -1)
    # rays_o = np.broadcast_to(c2w[:3, -1], np.shape(rays_d))
    # print(rays_d)
    # print(rays_o)
    # ii, jj = meshgrid_xy(torch.arange(3).float(), torch.arange(4, 7).float())
    # dirs_torch = torch.stack([(ii - W) * .5 / focal, -(jj - H) * .5 / focal, -torch.ones_like(ii)], -1)
    # c2w_torch = torch.eye(4)
    # c2w_torch[:3, :3] = 2 * c2w_torch[:3, :3]
    # rays_d_torch = torch.sum(dirs_torch[..., None, :] * c2w_torch[:3, :3], -1)
    # rays_o_torch = c2w_torch[:3, -1].expand(rays_d_torch.shape)
    # print(rays_d_torch)
    # print(rays_o_torch)
    # print(np.allclose(rays_d, rays_d_torch.cpu().numpy()))
    # print(np.allclose(rays_o, rays_o_torch.cpu().numpy()))

    # # get_rays(_torch) vs get_rays_np
    # H, W = 3, 3
    # focal = 10
    # c2w = np.eye(4)
    # c2w[:3, :3] = 2 * c2w[:3, :3]
    # # c2w_torch = torch.eye(4)
    # # c2w_torch[:3, :3] = 2 * c2w_torch[:3, :3]
    # rays_o, rays_d = get_rays_np(H, W, focal, c2w)
    # c2w_torch = torch.from_numpy(c2w)
    # rays_o_torch, rays_d_torch = get_rays(H, W, focal, c2w_torch)
    # print(np.allclose(rays_o, rays_o_torch.cpu().numpy()))
    # print(np.allclose(rays_d, rays_d_torch.cpu().numpy()))  # Assert fails, values look different.
    # print("Numpy version:")
    # print(rays_d)
    # print("PyTorch version:")
    # print(rays_d_torch.cpu().numpy())

    # Test backprop for sample_pdf()
    bins = torch.rand(2, 4)
    weights = torch.rand(2, 4)
    weights.requires_grad = True
    samples = sample_pdf(bins, weights, 10)
    print(samples)
