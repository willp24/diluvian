# -*- coding: utf-8 -*-


import numpy as np


class OctreeMatrix(object):
    def __init__(self, leaf_size, bounds, dtype, populator=None):
        self.leaf_size = np.asarray(leaf_size).astype('uint64')
        self.bounds = (bounds[0].astype('uint64'), bounds[1].astype('uint64'))
        self.dtype = np.dtype(dtype)
        self.populator = populator
        ceil_bounds = self.leaf_size*np.exp2(np.ceil(np.log2((self.bounds[1] - self.bounds[0]) /
                                                             self.leaf_size.astype('float64')))).astype('uint64').max()
        self.root_node = BranchNode(self, (self.bounds[0], self.bounds[0] + ceil_bounds), clip_bound=self.bounds[1])

    @property
    def shape(self):
        return tuple(self.root_node.get_size())

    def get_checked_np_key(self, key):
        if len(key) != 3:
            raise IndexError('Octrees may only be indexed in 3 dimensions')

        # Convert keys to two numpy arrays for ease.
        npkey = (np.zeros(3, dtype='uint64'), np.zeros(3, dtype='uint64'))
        for i, k in enumerate(key):
            if isinstance(k, slice):
                if k.step is not None:
                    raise IndexError('Octrees do not yet support step slicing')
                npkey[0][i] = k.start
                npkey[1][i] = k.stop
            else:
                npkey[0][i] = k
                npkey[1][i] = k + 1

        if np.any(np.less(npkey[0], self.bounds[0])) or \
           np.any(np.greater(npkey[1], self.bounds[1])) or \
           np.any(np.greater_equal(npkey[0], npkey[1])):
           raise IndexError('Invalid indices: outside bounds or empty interval: {} (bounds {})'.format(str(key), str(self.bounds)))

        return npkey

    def __getitem__(self, key):
        npkey = self.get_checked_np_key(key)

        return self.root_node[npkey]

    def __setitem__(self, key, value):
        npkey = self.get_checked_np_key(key)

        self.root_node[npkey] = value

    def get_volume(self):
        return self

    def replace_child(self, child, replacement):
        if child != self.root_node:
            raise ValueError('Attempt to replace unknown child')

        self.root_node = replacement


class Node(object):
    def __init__(self, parent, bounds, clip_bound=None):
        self.parent = parent
        self.bounds = (bounds[0].copy(), bounds[1].copy())
        self.clip_bound = clip_bound

    def get_intersection(self, key):
        return (np.maximum(self.bounds[0], key[0]),
                np.minimum(self.bounds[1], key[1]))

    def get_size(self):
        if self.clip_bound is not None:
            return self.clip_bound - self.bounds[0]
        return self.bounds[1] - self.bounds[0]

    def get_volume(self):
        return self.parent.get_volume()

    def replace(self, replacement):
        self.parent.replace_child(self, replacement)
        self.parent = None


class BranchNode(Node):
    def __init__(self, parent, bounds, **kwargs):
        super(BranchNode, self).__init__(parent, bounds, **kwargs)
        self.midpoint = (self.bounds[1] + self.bounds[0]) / 2
        self.children = [[[None for _ in range(2)] for _ in range(2)] for _ in range(2)]

    def get_children_mask(self, key):
        p = (np.less(key[0], self.midpoint),
             np.greater_equal(key[1], self.midpoint))

        # TODO must be some way to do combinatorial ops like this with numpy.
        return np.where([[[p[i][0] and p[j][1] and p[k][2] for k in range(2)] for j in range(2)] for i in range(2)])

    def get_child_bounds(self, i, j, k):
        mins = (self.bounds[0], self.midpoint)
        maxs = (self.midpoint, self.bounds[1])
        child_bounds = (np.array((mins[i][0], mins[j][1], mins[k][2])),
                        np.array((maxs[i][0], maxs[j][1], maxs[k][2])))
        if self.clip_bound is not None:
            clip_bound = np.minimum(child_bounds[1], self.clip_bound)
            if np.array_equal(clip_bound, child_bounds[1]):
                clip_bound = None
        else:
            clip_bound = None

        return (child_bounds, clip_bound)

    def __getitem__(self, key):
        inds = self.get_children_mask(key)

        for i, j, k in zip(*inds):
            if self.children[i][j][k] is None:
                self.populate_child(i, j, k)

        chunk = np.empty(tuple(key[1] - key[0]), self.get_volume().dtype)
        for i, j, k in zip(*inds):
            child = self.children[i][j][k]
            subchunk = child.get_intersection(key)
            ind = (subchunk[0] - key[0], subchunk[1] - key[0])
            chunk[ind[0][0]:ind[1][0],
                  ind[0][1]:ind[1][1],
                  ind[0][2]:ind[1][2]] = child[subchunk]

        return chunk

    def __setitem__(self, key, value):
        if (not hasattr(value, '__len__') or len(value) == 1) and \
           np.array_equal(key[0], self.bounds[0]) and \
           np.array_equal(key[1], self.clip_bound):
           self.replace(UniformBranchNode(self.parent, self.bounds, self.get_volume().dtype, value, clip_bound=self.clip_bound))
           return

        inds = self.get_children_mask(key)

        for i, j, k in zip(*inds):
            if self.children[i][j][k] is None:
                self.populate_child(i, j, k)

        for i, j, k in zip(*inds):
            child = self.children[i][j][k]
            subchunk = child.get_intersection(key)
            ind = (subchunk[0] - key[0], subchunk[1] - key[0])
            if isinstance(value, np.ndarray):
                child[subchunk] = value[ind[0][0]:ind[1][0],
                                        ind[0][1]:ind[1][1],
                                        ind[0][2]:ind[1][2]]
            else:
                child[subchunk] = value

    def populate_child(self, i, j, k):
        volume = self.get_volume()
        if volume.populator is None:
            raise ValueError('Attempt to retrieve unpopulated region without octree populator')

        child_bounds, child_clip_bound = self.get_child_bounds(i, j, k)
        child_size = child_bounds[1] - child_bounds[0]
        if np.any(np.less_equal(child_size, volume.leaf_size)):
            populator_bounds = [child_bounds[0].copy(), child_bounds[1].copy()]
            if child_clip_bound is not None:
                populator_bounds[1] = np.minimum(populator_bounds[1], child_clip_bound)
            data = volume.populator(populator_bounds).astype(volume.dtype)
            child = LeafNode(self, child_bounds, data)
        else:
            child = BranchNode(self, child_bounds, clip_bound=child_clip_bound)

        self.children[i][j][k] = child

    def replace_child(self, child, replacement):
        for i in range(2):
            for j in range(2):
                for k in range(2):
                    if child == self.children[i][j][k]:
                        self.children[i][j][k] = replacement
                        return

        raise ValueError('Attempt to replace unknown child')


class LeafNode(Node):
    def __init__(self, parent, bounds, data):
        super(LeafNode, self).__init__(parent, bounds)
        self.data = data.copy()

    def __getitem__(self, key):
        ind = (key[0] - self.bounds[0], key[1] - self.bounds[0])
        return self.data[ind[0][0]:ind[1][0],
                         ind[0][1]:ind[1][1],
                         ind[0][2]:ind[1][2]]

    def __setitem__(self, key, value):
        ind = (key[0] - self.bounds[0], key[1] - self.bounds[0])
        self.data[ind[0][0]:ind[1][0],
                  ind[0][1]:ind[1][1],
                  ind[0][2]:ind[1][2]] = value


class UniformNode(Node):
    def __init__(self, parent, bounds, dtype, value, **kwargs):
        super(UniformNode, self).__init__(parent, bounds, **kwargs)
        self.value = value
        self.dtype = dtype

    def __getitem__(self, key):
        return np.full(tuple(key[1] - key[0]), self.value, dtype=self.dtype)


class UniformBranchNode(UniformNode):
    def __setitem__(self, key, value):
        replacement = BranchNode(self.parent, self.bounds, clip_bound=self.clip_bound)
        volume = self.get_volume()
        for i in range(2):
            for j in range(2):
                for k in range(2):
                    child_bounds, child_clip_bound = replacement.get_child_bounds(i, j, k)
                    # If this child is entirely outside the clip bounds, it will never be accessed
                    # or populated and thus can be omitted.
                    if child_clip_bound is not None and np.any(np.greater(child_bounds[0], child_clip_bound)):
                        continue
                    child_size = child_bounds[1] - child_bounds[0]
                    if np.any(np.less_equal(child_size, volume.leaf_size)):
                        child = UniformLeafNode(replacement, child_bounds, self.dtype, self.value)
                    else:
                        child = UniformBranchNode(replacement, child_bounds, self.dtype, self.value, clip_bound=child_clip_bound)
                    replacement.children[i][j][k] = child
        self.replace(replacement)
        replacement[key] = value


class UniformLeafNode(UniformNode):
    def __setitem__(self, key, value):
        replacement = LeafNode(self.parent, self.bounds, self[self.bounds])
        self.replace(replacement)
        replacement[key] = value
