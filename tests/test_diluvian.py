#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
test_diluvian
----------------------------------

Tests for `diluvian` module.
"""


from __future__ import division

import numpy as np
from pathlib import Path
import shutil
import pyn5

from diluvian import octrees
from diluvian import regions
from diluvian import volumes
from diluvian.config import CONFIG
from diluvian.util import (
        binary_confusion_matrix,
        confusion_f_score,
        get_nonzero_aabb,
        )


def test_octree_bounds():
    clip_bounds = (np.zeros(3), np.array([11, 6, 5]))
    ot = octrees.OctreeVolume([5, 5, 5], clip_bounds, np.uint8)
    ot[clip_bounds[0][0]:clip_bounds[1][0],
       clip_bounds[0][1]:clip_bounds[1][1],
       clip_bounds[0][2]:clip_bounds[1][2]] = 6
    assert isinstance(ot.root_node, octrees.UniformNode), "Constant assignment should make root uniform."

    ot[8, 5, 4] = 5
    expected_mat = np.array([[[6], [6]], [[6], [5]]], dtype=np.uint8)
    assert np.array_equal(ot[7:9, 4:6, 4], expected_mat), "Assignment should break uniformity."

    expected_types = [[[octrees.BranchNode, None], [None, None]],
                      [[octrees.UniformBranchNode, None], [None, None]]]
    for i, col in enumerate(expected_types):
        for j, row in enumerate(col):
            for k, expected_type in enumerate(row):
                if expected_type is None:
                    assert ot.root_node.children[i][j][k] is None, "Clip bounds should make most nodes empty."
                else:
                    assert isinstance(ot.root_node.children[i][j][k], expected_type), "Nodes are wrong type."

    np.testing.assert_almost_equal(ot.fullness(), 2.0/3.0, err_msg='Octree fullness should be relative to clip bounds.')

    ot[10, 5, 4] = 5  # Break the remaining top-level uniform branch node.
    np.testing.assert_almost_equal(ot.fullness(), 1.0, err_msg='Octree fullness should be relative to clip bounds.')

    np.testing.assert_array_equal(ot.get_leaf_bounds()[1], clip_bounds[1],
                                  err_msg='Leaf bounds should be clipped to clip bounds')


def test_octree_map_copy():
    clip_bounds = (np.zeros(3), np.array([11, 6, 5]))
    ot = octrees.OctreeVolume([5, 5, 5], clip_bounds, np.uint8)
    ot[clip_bounds[0][0]:clip_bounds[1][0],
       clip_bounds[0][1]:clip_bounds[1][1],
       clip_bounds[0][2]:clip_bounds[1][2]] = 6

    ot[8, 5, 4] = 5

    def leaf_map(a):
        return a * -1

    def uniform_map(v):
        return v * 1.5

    cot = ot.map_copy(np.float32, leaf_map, uniform_map)
    for orig, copy in zip(ot.iter_leaves(), cot.iter_leaves()):
        np.testing.assert_almost_equal(copy.bounds[0], orig.bounds[0], err_msg='Copy leaves should have same bounds.')
        np.testing.assert_almost_equal(copy.bounds[1], orig.bounds[1], err_msg='Copy leaves should have same bounds.')
        np.testing.assert_almost_equal(copy.data, leaf_map(orig.data), err_msg='Copy leaves should be mapped.')
    expected_mat = np.array([[[9.], [-6.]], [[9.], [-5.]]], dtype=np.float32)
    assert np.array_equal(cot[7:9, 4:6, 4], expected_mat), 'Copy should have same uniformity.'


def test_region_moves():
    mock_image = np.zeros(tuple(CONFIG.model.training_subv_shape), dtype=np.float32)
    region = regions.Region(mock_image)
    mock_mask = np.zeros(tuple(CONFIG.model.output_fov_shape), dtype=np.float32)
    ctr = np.array(mock_mask.shape) // 2
    expected_moves = {}
    for i, move in enumerate(map(np.array, [(1, 0, 0), (-1, 0, 0),
                                            (0, 1, 0), (0, -1, 0),
                                            (0, 0, 1), (0, 0, -1)])):
        val = 0.1 * (i + 1)
        coord = ctr + (region.MOVE_DELTA * move) + np.array([2, 2, 2]) * (np.ones(3) - np.abs(move))
        mock_mask[tuple(coord.astype(np.int64))] = val
        expected_moves[tuple(move)] = val

    moves = region.get_moves(mock_mask)
    for move in moves:
        np.testing.assert_allclose(expected_moves[tuple(move['move'])], move['v'])

    # Test thick move check planes.
    mock_mask[:] = 0
    for i, move in enumerate(map(np.array, [(1, 0, 0), (-1, 0, 0),
                                            (0, 1, 0), (0, -1, 0),
                                            (0, 0, 1), (0, 0, -1)])):
        val = 0.15 * (i + 1)
        coord = ctr + ((region.MOVE_DELTA + 1) * move) + np.array([2, 2, 2]) * (np.ones(3) - np.abs(move))
        mock_mask[tuple(coord.astype(np.int64))] = val
        expected_moves[tuple(move)] = val

    region.move_check_thickness = 2
    moves = region.get_moves(mock_mask)
    for move in moves:
        np.testing.assert_allclose(expected_moves[tuple(move['move'])], move['v'])


def test_volume_transforms():
    mock_image = np.arange(64 * 64 * 64, dtype=np.uint8).reshape((64, 64, 64))
    mock_label = np.zeros((64, 64, 64), dtype=np.int64)

    v = volumes.Volume((1, 1, 1), image_data=mock_image, label_data=mock_label)
    pv = v.partition([1, 1, 2], [0, 0, 1])
    dpv = pv.downsample((4, 4, 1))

    np.testing.assert_array_equal(dpv.local_coord_to_world(np.array([2, 2, 2])), np.array([8, 8, 34]))
    np.testing.assert_array_equal(dpv.world_coord_to_local(np.array([8, 8, 34])), np.array([2, 2, 2]))

    svb = volumes.SubvolumeBounds(np.array((0, 0, 32), dtype=np.int64),
                                  np.array((4, 4, 33), dtype=np.int64))
    sv = v.get_subvolume(svb)

    dpsvb = volumes.SubvolumeBounds(np.array((0, 0, 0), dtype=np.int64),
                                    np.array((1, 1, 1), dtype=np.int64))
    dpsv = dpv.get_subvolume(dpsvb)

    np.testing.assert_array_equal(dpsv.image, sv.image.reshape((1, 4, 1, 4, 1, 1)).mean(5).mean(3).mean(1))


def test_volume_transforms_image_stacks():
    # stack info
    si = {
        "bounds": [28128, 31840, 4841],
        "resolution": [3.8, 3.8, 50],
        "tile_width": 512,
        "tile_height": 512,
        "translation": [0, 0, 0],
    }
    # tile stack parameters
    tsp = {
        "source_base_url": "https://neurocean.janelia.org/ssd-tiles-no-cache/0111-8/",
        "file_extension": "jpg",
        "tile_width": 512,
        "tile_height": 512,
        "tile_source_type": 4,
    }
    v = volumes.ImageStackVolume.from_catmaid_stack(si, tsp)
    pv = v.partition(
        [2, 1, 1], [1, 0, 0]
    )  # Note axes are flipped after volume initialization
    dpv = pv.downsample((50, 15.2, 15.2))

    np.testing.assert_array_equal(
        dpv.local_coord_to_world(np.array([2, 2, 2])), np.array([2422, 8, 8])
    )
    np.testing.assert_array_equal(
        dpv.world_coord_to_local(np.array([2422, 8, 8])), np.array([2, 2, 2])
    )

    svb = volumes.SubvolumeBounds(
        np.array((2420, 0, 0), dtype=np.int64),
        np.array((2421, 4, 4), dtype=np.int64),
    )
    sv = v.get_subvolume(svb)

    dpsvb = volumes.SubvolumeBounds(
        np.array((0, 0, 0), dtype=np.int64), np.array((1, 1, 1), dtype=np.int64)
    )
    dpsv = dpv.get_subvolume(dpsvb)

    np.testing.assert_array_equal(
        dpsv.image, sv.image.reshape((1, 4, 1, 4, 1, 1)).mean(5).mean(3).mean(1)
    )


def test_volume_transforms_n5_volume():
    # Create test n5 dataset
    test_dataset_path = Path("test.n5")
    if test_dataset_path.is_dir():
        shutil.rmtree(str(test_dataset_path.absolute()))
    pyn5.create_dataset("test.n5", "test", [10, 10, 10], [2, 2, 2], "UINT8")
    test_dataset = pyn5.open("test.n5", "test")

    test_data = np.zeros([10, 10, 10]).astype(int)
    x = np.linspace(0, 9, 10).reshape([10, 1, 1]).astype(int)
    test_data = test_data + x + x.transpose([1, 2, 0]) + x.transpose([2, 0, 1])

    block_starts = [(i % 5, i // 5 % 5, i // 25 % 5) for i in range(5 ** 3)]
    for block_start in block_starts:
        current_bound = list(
            map(slice, [2 * x for x in block_start], [2 * x + 2 for x in block_start])
        )
        flattened = test_data[current_bound].reshape(-1)
        try:
            test_dataset.write_block(block_start, flattened)
        except Exception as e:
            raise AssertionError("Writing to n5 failed! Could not create test dataset.\nError: {}".format(e))

    v = volumes.N5Volume("test.n5",
                         {"image": {"path": "test", "dtype": "UINT8"}},
                         bounds=[10, 10, 10],
                         resolution=[1, 1, 1])
    pv = v.partition(
        [2, 1, 1], [1, 0, 0]
    )  # Note axes are flipped after volume initialization
    dpv = pv.downsample((2, 2, 2))

    np.testing.assert_array_equal(
        dpv.local_coord_to_world(np.array([2, 2, 2])), np.array([9, 4, 4])
    )
    np.testing.assert_array_equal(
        dpv.world_coord_to_local(np.array([9, 4, 4])), np.array([2, 2, 2])
    )

    svb = volumes.SubvolumeBounds(
        np.array((5, 0, 0), dtype=np.int64), np.array((7, 2, 2), dtype=np.int64)
    )
    sv = v.get_subvolume(svb)

    dpsvb = volumes.SubvolumeBounds(
        np.array((0, 0, 0), dtype=np.int64), np.array((1, 1, 1), dtype=np.int64)
    )
    dpsv = dpv.get_subvolume(dpsvb)

    np.testing.assert_array_equal(
        dpsv.image, sv.image.reshape((1, 2, 1, 2, 1, 2)).mean(5).mean(3).mean(1)
    )

    # sanity check that test.n5 contains varying data
    svb2 = volumes.SubvolumeBounds(
        np.array((5, 0, 1), dtype=np.int64), np.array((7, 2, 3), dtype=np.int64)
    )
    sv2 = v.get_subvolume(svb2)
    assert not all(sv.image.flatten() == sv2.image.flatten())

    if test_dataset_path.is_dir():
        shutil.rmtree(str(test_dataset_path.absolute()))


def test_volume_identity_downsample_returns_self():
    resolution = (27, 185, 90)
    v = volumes.Volume(resolution, image_data=np.zeros((1, 1, 1)), label_data=np.zeros((1, 1, 1)))
    dv = v.downsample(resolution)

    assert v == dv


def test_nonzero_aabb():
    a = np.zeros([10, 10, 10], dtype=np.int32)
    a[8, 7, 6] = 1

    amin, amax = get_nonzero_aabb(a)
    np.testing.assert_array_equal(amin, [8, 7, 6])
    np.testing.assert_array_equal(amax, [9, 8, 7])

    a[6, 7, 8] = 1
    amin, amax = get_nonzero_aabb(a)
    np.testing.assert_array_equal(amin, [6, 7, 6])
    np.testing.assert_array_equal(amax, [9, 8, 9])


def test_confusion_matrix():
    a = np.zeros([3, 3, 3], dtype=np.bool)
    a[2, 2, :] = True
    b = np.ones([3, 3, 3], dtype=np.bool)
    b[:, 2, 2] = False

    cm = np.array([[2, 22], [1, 2]])
    np.testing.assert_array_equal(binary_confusion_matrix(a.flatten(), b.flatten()), cm)


def test_f1_score():
    a = np.array([[375695, 6409], [31208, 67419]])

    np.testing.assert_almost_equal(confusion_f_score(a, 1.0), 0.782, decimal=3)
    assert confusion_f_score(np.eye(2), 1.0) == 1.0
