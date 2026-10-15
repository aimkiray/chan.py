import copy
import datetime
import logging
import pickle
import sys
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Union

from BuySellPoint.BS_Point import CBS_Point
from ChanConfig import CChanConfig
from Common.CEnum import AUTYPE, DATA_SRC, KL_TYPE
from Common.ChanException import CChanException, ErrCode
from Common.CTime import CTime
from Common.func_util import check_kltype_order, kltype_lte_day
from DataAPI.CommonStockAPI import CCommonStockApi
from KLine.KLine_List import CKLine_List
from KLine.KLine_Unit import CKLine_Unit

_logger = logging.getLogger(__name__)


class CChan:
    def __init__(
        self,
        code,
        begin_time=None,
        end_time=None,
        data_src: Union[DATA_SRC, str] = DATA_SRC.BAO_STOCK,
        lv_list=None,
        config=None,
        autype: AUTYPE = AUTYPE.QFQ,
    ):
        if lv_list is None:
            lv_list = [KL_TYPE.K_DAY, KL_TYPE.K_60M]
        check_kltype_order(lv_list)  # lv_list顺序从高到低
        self.code = code
        self.begin_time = str(begin_time) if isinstance(begin_time, datetime.date) else begin_time
        self.end_time = str(end_time) if isinstance(end_time, datetime.date) else end_time
        self.autype = autype
        self.data_src = data_src
        self.lv_list: List[KL_TYPE] = lv_list

        if config is None:
            config = CChanConfig()
        self.conf = config

        self.do_init()

        if not config.trigger_step:
            for _ in self.load():
                ...

    def __deepcopy__(self, memo):
        cls = self.__class__
        obj: CChan = cls.__new__(cls)
        memo[id(self)] = obj
        obj.code = self.code
        obj.begin_time = self.begin_time
        obj.end_time = self.end_time
        obj.autype = self.autype
        obj.data_src = self.data_src
        obj.lv_list = copy.deepcopy(self.lv_list, memo)
        obj.conf = copy.deepcopy(self.conf, memo)
        obj.kl_misalign_cnt = self.kl_misalign_cnt
        obj.kl_inconsistent_detail = copy.deepcopy(self.kl_inconsistent_detail, memo)
        obj.g_kl_iter = defaultdict(list)  # 迭代器不可深拷贝；拷贝侧如需继续加载应重新装载
        obj.data_warnings = copy.deepcopy(getattr(self, "data_warnings", []), memo)
        if hasattr(self, 'klu_cache'):
            obj.klu_cache = copy.deepcopy(self.klu_cache, memo)
        if hasattr(self, 'klu_last_t'):
            obj.klu_last_t = copy.deepcopy(self.klu_last_t, memo)
        obj.kl_datas = {}
        for kl_type, ckline in self.kl_datas.items():
            obj.kl_datas[kl_type] = copy.deepcopy(ckline, memo)
        for kl_type, ckline in self.kl_datas.items():
            for klc in ckline:
                for klu in klc.lst:
                    assert id(klu) in memo
                    if klu.sup_kl:
                        memo[id(klu)].sup_kl = memo[id(klu.sup_kl)]
                    memo[id(klu)].sub_kl_list = [memo[id(sub_kl)] for sub_kl in klu.sub_kl_list]
        return obj

    def do_init(self):
        # 重置全部加载期状态，保证 step_load/重复 load 可重入（无计数器/迭代器/告警残留）
        self.kl_datas: Dict[KL_TYPE, CKLine_List] = {}
        for idx in range(len(self.lv_list)):
            self.kl_datas[self.lv_list[idx]] = CKLine_List(self.lv_list[idx], conf=self.conf)
        self.kl_misalign_cnt = 0
        self.kl_inconsistent_detail = defaultdict(list)
        self.g_kl_iter = defaultdict(list)
        self.data_warnings = []
        self.klu_cache: List[Optional[CKLine_Unit]] = [None for _ in self.lv_list]
        self.klu_last_t = [CTime(1980, 1, 1, 0, 0) for _ in self.lv_list]

    def load_stock_data(self, stockapi_instance: CCommonStockApi, lv) -> Iterable[CKLine_Unit]:
        for KLU_IDX, klu in enumerate(stockapi_instance.get_kl_data()):
            klu.set_idx(KLU_IDX)
            klu.kl_type = lv
            yield klu

    def get_load_stock_iter(self, stockapi_cls, lv):
        stockapi_instance = stockapi_cls(code=self.code, k_type=lv, begin_date=self.begin_time, end_date=self.end_time, autype=self.autype)
        
        # We need to capture warnings after iteration. 
        # Since this is a generator, we wrap it.
        def wrapped_iter():
            first_item = True
            for klu in self.load_stock_data(stockapi_instance, lv):
                if first_item:
                    first_item = False
                    # Check for data gap (Requested vs Actual)
                    try:
                        if self.begin_time:
                            # Try to parse self.begin_time
                            begin_dt = None
                            # Clean up string first (remove potential quotes)
                            begin_str = str(self.begin_time).strip("'\"")
                            
                            # Common formats
                            formats = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"]
                            # Try to handle formats with microseconds if present
                            if "." in begin_str and "-" in begin_str:
                                formats.insert(0, "%Y-%m-%d %H:%M:%S.%f")
                                
                            for fmt in formats:
                                try:
                                    begin_dt = datetime.datetime.strptime(begin_str, fmt)
                                    break
                                except ValueError:
                                    continue
                            
                            if begin_dt:
                                # klu.time is CTime, convert to datetime（保留时分秒，避免带时间的 begin_time 被偏保守地截断到天）
                                klu_dt = datetime.datetime(klu.time.year, klu.time.month, klu.time.day,
                                                           klu.time.hour, klu.time.minute, klu.time.second)

                                # Calculate gap in days
                                delta_days = (klu_dt - begin_dt).days
                                
                                # Debug print
                                # print(f"[DEBUG] Check Gap: Raw={self.begin_time} Parsed={begin_dt}, Actual={klu_dt}, Gap={delta_days} days")

                                # If gap is significant (e.g., > 10 days), add warning
                                # 10 days allows for long holidays (e.g. CNY)
                                if delta_days > 10:
                                    msg = f"Data Range Warning: Requested start {begin_str[:10]}, but actual data starts from {klu.time}. (Gap: {delta_days} days). This may be due to data source limitations."
                                    # Avoid duplicate warnings if possible
                                    if not any(msg in w for w in self.data_warnings):
                                        self.data_warnings.append(msg)
                    except Exception as e:
                        # Don't fail the analysis just because check failed
                        _logger.warning(f"[Warning Check Error] {e}")

                yield klu

            # Check for warnings after data is exhausted
            if getattr(stockapi_instance, 'warnings', None):
                self.data_warnings.extend(stockapi_instance.warnings)
            
        return wrapped_iter()

    def add_lv_iter(self, lv_idx, klu_iter):
        if isinstance(lv_idx, int):
            self.g_kl_iter[self.lv_list[lv_idx]].append(klu_iter)
        else:
            self.g_kl_iter[lv_idx].append(klu_iter)

    def _drop_lv(self, lv: KL_TYPE):
        """迭代期永久摘除一个级别：数据源在迭代中途才抛 SRC_DATA_NOT_FOUND 时
        auto_skip_illegal_sub_lv 的兜底。该级别已装载数据全部丢弃、迭代器废弃，
        本趟加载不再恢复（有损降级，下次 load()/do_init 才按新 lv_list 重建）。

        引用清理（缺一会使 __deepcopy__ 的 memo 定址抛 KeyError）：
        - 次次级已入列 klu：sup_kl 曾指向被摘级别的孤儿 klu，提升为祖父级的直接子级
          —— sup_kl 重挂祖父 klu，并在祖父 sub_kl_list 原位置 splice 替换，双向一致。
          这与"构造期就跳过该级别、次次级直接挂到父级"的结构口径一致；进行中父 klu
          因获得提升子级而不会被 check_kl_align 误记 misalign（仅当提升后仍为空、即
          该窗口本就无次次级数据时才计数，与构造期 skip 口径相同）。
        - 父级 sub_kl_list / klu_cache 槽位中残留的被摘成员兜底剔除（正常已被上一步
          覆盖，此处防未入列缓存 klu 等边缘形态）。
        - g_kl_iter / kl_datas / lv_list / klu_cache / klu_last_t 同步移除该级别槽位。
        """
        pos = self.lv_list.index(lv)
        if pos <= 0:  # 最高级别不可跳过
            return
        # 1) 遍历被摘级别已入列 klu：其下级 klu 提升到各自祖父级
        dropped_list = self.kl_datas.get(lv)
        if dropped_list is not None:
            for klc in dropped_list.lst:
                for dropped_klu in klc.lst:
                    grand = dropped_klu.sup_kl
                    if grand is not None:
                        try:
                            idx = grand.sub_kl_list.index(dropped_klu)
                            grand.sub_kl_list[idx:idx + 1] = dropped_klu.sub_kl_list
                        except ValueError:  # 父表未收录该成员（理论不可达），并入兜底
                            grand.sub_kl_list.extend(dropped_klu.sub_kl_list)
                    for child in dropped_klu.sub_kl_list:
                        child.sup_kl = grand
                    dropped_klu.sub_kl_list = []
        # 2) 残余兜底：父级 sub_kl_list 与 klu_cache 缓存 klu 中仍指向被摘级别的引用
        parent_list = self.kl_datas.get(self.lv_list[pos - 1])
        if parent_list is not None:
            for klc in parent_list.lst:
                for parent_klu in klc.lst:
                    if any(sub.kl_type == lv for sub in parent_klu.sub_kl_list):
                        parent_klu.sub_kl_list = [sub for sub in parent_klu.sub_kl_list if sub.kl_type != lv]
        cache_lst = getattr(self, 'klu_cache', None)
        if isinstance(cache_lst, list):
            for cached in cache_lst:
                if cached is None:
                    continue
                if cached.sup_kl is not None and getattr(cached.sup_kl, 'kl_type', None) == lv:
                    cached.sup_kl = cached.sup_kl.sup_kl
                if any(sub.kl_type == lv for sub in cached.sub_kl_list):
                    cached.sub_kl_list = [sub for sub in cached.sub_kl_list if sub.kl_type != lv]
        # 3) 摘除迭代器/数据/槽位
        self.g_kl_iter.pop(lv, None)
        self.kl_datas.pop(lv, None)
        self.lv_list.pop(pos)
        for lst_name in ('klu_cache', 'klu_last_t'):
            lst = getattr(self, lst_name, None)
            if isinstance(lst, list) and len(lst) > pos:
                lst.pop(pos)

    def get_next_lv_klu(self, lv_idx):
        lv = self.lv_list[lv_idx] if isinstance(lv_idx, int) else lv_idx
        while len(self.g_kl_iter[lv]) > 0:
            try:
                return self.g_kl_iter[lv][0].__next__()
            except StopIteration:
                self.g_kl_iter[lv] = self.g_kl_iter[lv][1:]
            except CChanException as e:
                # 数据源在迭代期才抛 SRC_DATA_NOT_FOUND 时，同样按 auto_skip_illegal_sub_lv 跳过该级别
                if self.conf.auto_skip_illegal_sub_lv and e.errcode == ErrCode.SRC_DATA_NOT_FOUND \
                        and lv in self.lv_list and self.lv_list.index(lv) > 0:
                    if self.conf.print_warning:
                        _logger.warning(f"[WARNING-{self.code}]{lv}级别获取数据失败，跳过")
                    self._drop_lv(lv)
                    raise StopIteration from e
                raise
        raise StopIteration

    def step_load(self):
        assert self.conf.trigger_step
        self.do_init()  # 清空数据，防止再次重跑没有数据
        yielded = False  # 是否曾经返回过结果
        for idx, snapshot in enumerate(self.load(self.conf.trigger_step)):
            if idx < self.conf.skip_step:
                continue
            yield snapshot
            yielded = True
        if not yielded:
            yield self

    def trigger_load(self, inp):
        # {type: [klu, ...]}
        if not hasattr(self, 'klu_cache'):
            self.klu_cache: List[Optional[CKLine_Unit]] = [None for _ in self.lv_list]
        if not hasattr(self, 'klu_last_t'):
            self.klu_last_t = [CTime(1980, 1, 1, 0, 0) for _ in self.lv_list]
        for lv_idx, lv in enumerate(self.lv_list):
            if lv not in inp:
                if lv_idx == 0:
                    raise CChanException(f"最高级别{lv}没有传入数据", ErrCode.NO_DATA)
                continue
            # 先校验再消费：生成器/dict 等非 list 入参直接报参数错，不允许先被 for 循环吞掉
            if not isinstance(inp[lv], list):
                raise CChanException(f"trigger_load入参{lv}级别的数据必须是list，实际类型={type(inp[lv])}", ErrCode.PARA_ERROR)
            for klu in inp[lv]:
                klu.kl_type = lv
            self.add_lv_iter(lv, iter(inp[lv]))
        for _ in self.load_iterator(lv_idx=0, parent_klu=None, step=False):
            ...
        if not self.conf.trigger_step:  # 非回放模式全部算完之后才算一次中枢和线段
            for lv in self.lv_list:
                self.kl_datas[lv].cal_seg_and_zs()

    def init_lv_klu_iter(self, stockapi_cls):
        # 为了跳过一些获取数据失败的级别
        lv_klu_iter = []
        valid_lv_list = []
        for lv in self.lv_list:
            try:
                lv_klu_iter.append(self.get_load_stock_iter(stockapi_cls, lv))
                valid_lv_list.append(lv)
            except CChanException as e:
                if e.errcode == ErrCode.SRC_DATA_NOT_FOUND and self.conf.auto_skip_illegal_sub_lv:
                    if self.conf.print_warning:
                        _logger.warning(f"[WARNING-{self.code}]{lv}级别获取数据失败，跳过")
                    del self.kl_datas[lv]
                    continue
                raise
        self.lv_list = valid_lv_list
        return lv_klu_iter

    def GetStockAPI(self):
        _dict = {}
        if self.data_src == DATA_SRC.BAO_STOCK:
            from DataAPI.BaoStockAPI import CBaoStock
            _dict[DATA_SRC.BAO_STOCK] = CBaoStock
        elif self.data_src == DATA_SRC.CCXT:
            from DataAPI.ccxt import CCXT
            _dict[DATA_SRC.CCXT] = CCXT
        elif self.data_src == DATA_SRC.CSV:
            from DataAPI.csvAPI import CSV_API
            _dict[DATA_SRC.CSV] = CSV_API
        elif self.data_src in (DATA_SRC.AK_SHARE, DATA_SRC.AKSHARE):
            from DataAPI.AkShareAPI import CAkShare
            _dict[DATA_SRC.AK_SHARE] = CAkShare
            _dict[DATA_SRC.AKSHARE] = CAkShare
        elif self.data_src == DATA_SRC.TDX:
            from DataAPI.TdxAPI import CTdxStockApi
            _dict[DATA_SRC.TDX] = CTdxStockApi
        elif self.data_src == DATA_SRC.CLICK_HOUSE:
            from DataAPI.ClickHouseAPI import CClickHouseAPI
            _dict[DATA_SRC.CLICK_HOUSE] = CClickHouseAPI
        if self.data_src in _dict:
            return _dict[self.data_src]
        if not isinstance(self.data_src, str):
            raise CChanException(f"load src type error: unsupported data_src={self.data_src!r}", ErrCode.SRC_DATA_TYPE_ERR)
        if self.data_src.find("custom:") < 0:
            raise CChanException("load src type error", ErrCode.SRC_DATA_TYPE_ERR)
        package_info = self.data_src.split(":", 1)[1]
        package_fields = package_info.split(".")
        if len(package_fields) != 2 or not all(package_fields):
            raise CChanException(f"custom data_src format error: {self.data_src}, expect 'custom:<module>.<class>'",
                                 ErrCode.SRC_DATA_TYPE_ERR)
        package_name, cls_name = package_fields
        import importlib
        try:
            module = importlib.import_module(f"DataAPI.{package_name}")
        except ImportError as e:
            raise CChanException(f"custom data_src import failed: {self.data_src} ({e})",
                                 ErrCode.SRC_DATA_TYPE_ERR) from e
        try:
            return getattr(module, cls_name)
        except AttributeError as e:
            raise CChanException(f"custom data_src class not found: {self.data_src} ({e})",
                                 ErrCode.SRC_DATA_TYPE_ERR) from e

    def load(self, step=False):
        stockapi_cls = self.GetStockAPI()
        self.do_init()  # 幂等：重复调用 load 时清除上次残留的迭代器/计数器
        try:
            stockapi_cls.do_init()
            for lv_idx, klu_iter in enumerate(self.init_lv_klu_iter(stockapi_cls)):
                self.add_lv_iter(lv_idx, klu_iter)
            self.klu_cache = [None for _ in self.lv_list]
            self.klu_last_t = [CTime(1980, 1, 1, 0, 0) for _ in self.lv_list]

            yield from self.load_iterator(lv_idx=0, parent_klu=None, step=step)  # 计算入口
            if not step:  # 非回放模式全部算完之后才算一次中枢和线段
                for lv in self.lv_list:
                    self.kl_datas[lv].cal_seg_and_zs()
        finally:
            stockapi_cls.do_close()
        if len(self[0]) == 0:
            raise CChanException("最高级别没有获得任何数据", ErrCode.NO_DATA)

    def set_klu_parent_relation(self, parent_klu, kline_unit, cur_lv, lv_idx):
        if self.conf.kl_data_check and kltype_lte_day(cur_lv) and kltype_lte_day(self.lv_list[lv_idx-1]):
            self.check_kl_consitent(parent_klu, kline_unit)
        parent_klu.add_children(kline_unit)
        kline_unit.set_parent(parent_klu)

    def add_new_kl(self, cur_lv: KL_TYPE, kline_unit):
        try:
            self.kl_datas[cur_lv].add_single_klu(kline_unit)
        except Exception:
            if self.conf.print_err_time:
                _logger.error(f"[ERROR-{self.code}]在计算{kline_unit.time}K线时发生错误!")
            raise

    def try_set_klu_idx(self, lv_idx: int, kline_unit: CKLine_Unit):
        if kline_unit.idx >= 0:
            return
        if len(self[lv_idx]) == 0:
            kline_unit.set_idx(0)
        else:
            kline_unit.set_idx(self[lv_idx][-1][-1].idx + 1)

    def load_iterator(self, lv_idx, parent_klu, step):
        # K线时间天级别以下描述的是结束时间，如60M线，每天第一根是10点30的
        # 天以上是当天日期
        cur_lv = self.lv_list[lv_idx]
        pre_klu = self[lv_idx][-1][-1] if len(self[lv_idx]) > 0 and len(self[lv_idx][-1]) > 0 else None
        while True:
            if self.klu_cache[lv_idx]:
                kline_unit = self.klu_cache[lv_idx]
                assert kline_unit is not None
                self.klu_cache[lv_idx] = None
            else:
                try:
                    kline_unit = self.get_next_lv_klu(lv_idx)
                    self.try_set_klu_idx(lv_idx, kline_unit)
                    if not kline_unit.time > self.klu_last_t[lv_idx]:
                        raise CChanException(f"kline time err, cur={kline_unit.time}, last={self.klu_last_t[lv_idx]}, or refer to quick_guide.md, try set auto=False in the CTime returned by your data source class", ErrCode.KL_NOT_MONOTONOUS)
                    self.klu_last_t[lv_idx] = kline_unit.time
                except StopIteration:
                    break

            if parent_klu and kline_unit.time > parent_klu.time:
                self.klu_cache[lv_idx] = kline_unit
                break
            kline_unit.set_pre_klu(pre_klu)
            pre_klu = kline_unit
            self.add_new_kl(cur_lv, kline_unit)
            if parent_klu:
                self.set_klu_parent_relation(parent_klu, kline_unit, cur_lv, lv_idx)
            if lv_idx != len(self.lv_list)-1:
                for _ in self.load_iterator(lv_idx+1, kline_unit, step):
                    ...
                self.check_kl_align(kline_unit, lv_idx)
            if lv_idx == 0 and step:
                yield self

    def check_kl_consitent(self, parent_klu, sub_klu):
        if parent_klu.time.year != sub_klu.time.year or \
           parent_klu.time.month != sub_klu.time.month or \
           parent_klu.time.day != sub_klu.time.day:
            self.kl_inconsistent_detail[str(parent_klu.time)].append(sub_klu.time)
            if self.conf.print_warning:
                _logger.warning(f"[WARNING-{self.code}]父级别时间是{parent_klu.time}，次级别时间却是{sub_klu.time}")
            # 按不一致条数（而非不同父日桶数）统计
            if sum(len(v) for v in self.kl_inconsistent_detail.values()) >= self.conf.max_kl_inconsistent_cnt:
                raise CChanException(f"父&子级别K线时间不一致条数超过{self.conf.max_kl_inconsistent_cnt}！！", ErrCode.KL_TIME_INCONSISTENT)

    def check_kl_align(self, kline_unit, lv_idx):
        # 递归期间末级可能被 _drop_lv 摘除，此时 lv_idx 已成末级：
        # 与"该次级别从未存在"的构造期口径一致，不再记 misalign
        if lv_idx >= len(self.lv_list) - 1:
            return
        if self.conf.kl_data_check and len(kline_unit.sub_kl_list) == 0:
            self.kl_misalign_cnt += 1
            if self.conf.print_warning:
                # 迭代期级别可能已被 auto_skip 移除，取不到时兜底
                sub_lv = self.lv_list[lv_idx + 1] if lv_idx + 1 < len(self.lv_list) else "?"
                _logger.warning(f"[WARNING-{self.code}]当前{kline_unit.time}没在次级别{sub_lv}找到K线！！")
            if self.kl_misalign_cnt >= self.conf.max_kl_misalgin_cnt:
                raise CChanException(f"在次级别找不到K线条数超过{self.conf.max_kl_misalgin_cnt}！！", ErrCode.KL_DATA_NOT_ALIGN)

    def __getitem__(self, n) -> CKLine_List:
        if isinstance(n, KL_TYPE):
            return self.kl_datas[n]
        elif isinstance(n, int):
            return self.kl_datas[self.lv_list[n]]
        else:
            raise CChanException("unsupported query type", ErrCode.COMMON_ERROR)

    def get_bsp(self, idx=None) -> List[CBS_Point]:
        _logger.warning('[deprecated] use get_latest_bsp instead')
        if idx is not None:
            return self[idx].bs_point_lst.getSortedBspList()
        if len(self.lv_list) != 1:
            raise CChanException(f"lv_list长度不为1({self.lv_list})，必须显式指定idx", ErrCode.PARA_ERROR)
        return self[0].bs_point_lst.getSortedBspList()

    def get_latest_bsp(self, idx=None, number=1) -> List[CBS_Point]:
        # number=0则取全部bsp，从最新到最旧排序
        if idx is not None:
            return self[idx].bs_point_lst.get_latest_bsp(number)
        if len(self.lv_list) != 1:
            raise CChanException(f"lv_list长度不为1({self.lv_list})，必须显式指定idx", ErrCode.PARA_ERROR)
        return self[0].bs_point_lst.get_latest_bsp(number)

    def chan_dump_pickle(self, file_path):
        _pre_limit = sys.getrecursionlimit()
        sys.setrecursionlimit(0x100000)
        try:
            for kl_list in self.kl_datas.values():
                for klc in kl_list.lst:
                    for klu in klc.lst:
                        klu.pre = None
                        klu.next = None
                    klc.set_pre(None)
                    klc.set_next(None)
                for bi in kl_list.bi_list:
                    bi.pre = None
                    bi.next = None
                for seg in kl_list.seg_list:
                    seg.pre = None
                    seg.next = None

                for segseg in kl_list.segseg_list:
                    segseg.pre = None
                    segseg.next = None

            with open(file_path, "wb") as f:
                pickle.dump(self, f)
        finally:
            # dump 中途失败也要恢复链路指针与递归深度限制，避免对象损坏和全局副作用残留
            sys.setrecursionlimit(_pre_limit)
            self.chan_pickle_restore()

    @staticmethod
    def chan_load_pickle(file_path) -> 'CChan':
        with open(file_path, "rb") as f:
            chan = pickle.load(f)
        chan.chan_pickle_restore()

        return chan

    def chan_pickle_restore(self):
        for kl_list in self.kl_datas.values():
            last_klu = None
            last_klc = None
            last_bi = None
            last_seg = None
            last_segseg = None
            for klc in kl_list.lst:
                for klu in klc.lst:
                    klu.pre = last_klu
                    if last_klu:
                        last_klu.next = klu
                    last_klu = klu
                klc.set_pre(last_klc)
                if last_klc:
                    last_klc.set_next(klc)
                last_klc = klc
            for bi in kl_list.bi_list:
                bi.pre = last_bi
                if last_bi:
                    last_bi.next = bi
                last_bi = bi
            for seg in kl_list.seg_list:
                seg.pre = last_seg
                if last_seg:
                    last_seg.next = seg
                last_seg = seg
            for segseg in kl_list.segseg_list:
                segseg.pre = last_segseg
                if last_segseg:
                    last_segseg.next = segseg
                last_segseg = segseg
