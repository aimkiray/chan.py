from typing import Optional


class CFeatures:
    def __init__(self, initFeat=None):
        self.__features = {} if initFeat is None else dict(initFeat)

    def items(self):
        yield from self.__features.items()

    def keys(self):
        return self.__features.keys()

    def __contains__(self, k):
        return k in self.__features

    def __len__(self):
        return len(self.__features)

    def get(self, k, default=None):
        return self.__features.get(k, default)

    def __getitem__(self, k):
        return self.__features[k]

    def add_feat(self, inp1, inp2: Optional[float] = None):
        """add_feat(dict)：批量合并特征映射；add_feat(k, v)：单值写入。
        注意 add_feat(k, None) 会走批量分支语义，要存 None 值请用 set(k, None)。"""
        if inp2 is None:
            self.__features.update(inp1)
        else:
            self.__features.update({inp1: inp2})

    def set(self, k, v):
        """单值写入（v 可为 None）。"""
        self.__features[k] = v
