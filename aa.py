"""
    Functions for various anti-aliasing functions and wrappers
"""
import kagefunc as kgf
from vsTAAmbk import TAAmbk
from vsutil import get_subsampling, get_w, get_y, join, split

import vapoursynth as vs

from . import util

core = vs.core


def nneedi3_clamp(clip: vs.VideoNode, strength: int = 1,
                  mask: vs.VideoNode = None, ret_mask: bool = False,
                  show_mask: bool = False,
                  opencl: bool = False) -> vs.VideoNode:
    funcname = "nneedi3_clamp"
    """
    Script written by Zastin. What it does is clamp the "change" done by eedi3 to the "change" of nnedi3.
    This should fix every issue created by eedi3. For example: https://i.imgur.com/hYVhetS.jpg

    :param strength:            Set threshold strength
    :param mask:                Allows for user to use their own mask
    :param ret_mask: bool:      Replace default mask with a retinex edgemask
    :param show_mask: bool:     Return mask
    :param opencl: bool:        Opencl acceleration
    """
    bits = clip.format.bits_per_sample - 8
    thr = strength * (1 >> bits)
    strong = TAAmbk(clip, aatype='Eedi3', alpha=0.25, beta=0.5, gamma=40, nrad=2, mdis=20, mtype=0,
                    opencl=opencl)
    weak = TAAmbk(clip, aatype='Nnedi3', nsize=3, nns=3, qual=1, mtype=0, opencl=opencl)
    expr = 'x z - y z - * 0 < y x y {0} + min y {0} - max ?'.format(thr)

    if clip.format.num_planes > 1:
        expr = [expr, '']
    aa = core.std.Expr([strong, weak, clip], expr)

    if mask:
        merged = clip.std.MaskedMerge(aa, mask, planes=0)
    elif ret_mask:
        mask = kgf.retinex_edgemask(clip, 1).std.Binarize()
        merged = clip.std.MaskedMerge(aa, mask, planes=0)
    else:
        mask = clip.std.Prewitt(planes=0).std.Binarize(planes=0).std.Maximum(planes=0).std.Convolution([1] * 9, planes=0)
        mask = get_y(mask)
        merged = clip.std.MaskedMerge(aa, mask, planes=0)

    if show_mask:
        return mask
    return merged if clip.format.color_family == vs.GRAY else core.std.ShufflePlanes([merged, clip], [0, 1, 2], vs.YUV)


def transpose_aa(clip: vs.VideoNode,
                 eedi3: bool = False) -> vs.VideoNode:
    funcname = "transpose_aa"
    """
    Function written by Zastin and modified by me.
    Performs anti-aliasing over a clip by using Nnedi3, transposing, using Nnedi3 again, and transposing a final time.
    This results in overall stronger anti-aliasing.
    Useful for shows like Yuru Camp with bad lineart problems.

    :param eedi3: bool:     Use eedi3 for the interpolation instead

    """
    clip_y = get_y(clip)

    if eedi3:
        def _aa(clip_y):
            clip_y = clip_y.std.Transpose()
            clip_y = clip_y.eedi3m.EEDI3(0, 1, 0, 0.5, 0.2)
            clip_y = clip_y.znedi3.nnedi3(1, 0, 0, 3, 4, 2)
            clip_y = clip_y.resize.Spline36(clip.height, clip.width, src_top=.5)
            clip_y = clip_y.std.Transpose()
            clip_y = clip_y.eedi3m.EEDI3(0, 1, 0, 0.5, 0.2)
            clip_y = clip_y.znedi3.nnedi3(1, 0, 0, 3, 4, 2)
            return clip_y.resize.Spline36(clip.width, clip.height, src_top=.5)
    else:
        def _aa(clip_y):
            clip_y = clip_y.std.Transpose()
            clip_y = clip_y.nnedi3.nnedi3(0, 1, 0, 3, 3, 2)
            clip_y = clip_y.nnedi3.nnedi3(1, 0, 0, 3, 3, 2)
            clip_y = clip_y.resize.Spline36(clip.height, clip.width, src_top=.5)
            clip_y = clip_y.std.Transpose()
            clip_y = clip_y.nnedi3.nnedi3(0, 1, 0, 3, 3, 2)
            clip_y = clip_y.nnedi3.nnedi3(1, 0, 0, 3, 3, 2)
            return clip_y.resize.Spline36(clip.width, clip.height, src_top=.5)

    def _csharp(flt, clip):
        blur = core.std.Convolution(flt, [1] * 9)
        return core.std.Expr([flt, clip, blur], 'x y < x x + z - x max y min x x + z - x min y max ?')

    aaclip = _aa(clip_y)
    aaclip = _csharp(aaclip, clip_y).rgvs.Repair(clip_y, 13)

    return aaclip if clip.format.color_family is vs.GRAY else core.std.ShufflePlanes([aaclip, clip], [0, 1, 2], vs.YUV)


def upscaled_sraa(clip: vs.VideoNode,
                  rfactor: float = 1.5,
                  rep: int = None,
                  h: int = None, ar = None,
                  sharp_downscale: bool = False) -> vs.VideoNode:
    funcname = "upscaled_sraa"
    """
    Another AA written by Zastin and modified by me.
    Performs an upscaled single-rate AA to deal with heavy aliasing.
    Useful for Web rips, where the source quality is not good enough to descale,
    but you still want to deal with some bad aliasing and lineart.
    :param rfactor: float:  Image enlargement factor. 1.3..2 makes it comparable in strength to vsTAAmbk
                            It is not recommended to go below 1.3
    :param rep: int:        Repair mode
    :param h: int:          Set custom height. Width and aspect ratio are auto-calculated
    """
    planes = split(clip)

    nnargs = dict(nsize=0, nns=4, qual=2)
    eeargs = dict(alpha=0.2, beta=0.6, gamma=40, nrad=2, mdis=20) # TAAmbk defaults are 0.5, 0.2, 20, 3, 30

    ssw = round( clip.width  * rfactor )
    ssh = round( clip.height * rfactor )

    while ssw % 2:
        ssw += 1
    while ssh % 2:
        ssh += 1

    if h:
        if not ar:
            ar = clip.width / clip.height
        w = get_w(h, aspect_ratio=ar)
    else:
        w, h = clip.width, clip.height

    # Nnedi3 upscale from source height to source height * rounding (Default 1.5)
    up_y = core.nnedi3.nnedi3(planes[0], 0, 1, 0, **nnargs)
    up_y = core.resize.Spline36(up_y, height=ssh, src_top=.5)
    up_y = core.std.Transpose(up_y)
    up_y = core.nnedi3.nnedi3(up_y, 0, 1, 0, **nnargs)
    up_y = core.resize.Spline36(up_y, height=ssw, src_top=.5)

    # Single-rate AA
    aa_y = core.eedi3m.EEDI3(up_y, 0, 0, 0, **eeargs, sclip=core.nnedi3.nnedi3(up_y, 0, 0, 0, **nnargs))
    aa_y = core.std.Transpose(aa_y)
    aa_y = core.eedi3m.EEDI3(aa_y, 0, 0, 0, **eeargs, sclip=core.nnedi3.nnedi3(aa_y, 0, 0, 0, **nnargs))

    # Back to source clip height or given height
    scaled = core.fmtc.resample(aa_y, w, h, kernel='gauss', invks=True, invkstaps=2, taps=1, a1=32) if sharp_downscale else core.resize.Spline36(aa_y, w, h)

    if rep:
        scaled = util.pick_repair(scaled)(scaled, planes[0].resize.Spline36(w, h), rep)
    return scaled if clip.format.color_family is vs.GRAY else core.std.ShufflePlanes([scaled, clip], [0, 1, 2], vs.YUV)