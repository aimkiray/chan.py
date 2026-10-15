import copy
import sys
from pathlib import Path
from typing import List

# Debug/没有__init__.py，直接 python Debug/strategy_demo3.py 运行时
# sys.path只含本目录，需显式把仓库根加入才能导入Chan等核心模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Chan import CChan
from ChanConfig import CChanConfig
from Common.CEnum import AUTYPE, DATA_FIELD, DATA_SRC, KL_TYPE
from DataAPI.BaoStockAPI import CBaoStock
from KLine.KLine_Unit import CKLine_Unit

# A股60M K线的结束时刻（分钟数）：10:30 / 11:30 / 14:00 / 15:00
A_SHARE_60M_END_MINUTES = (10 * 60 + 30, 11 * 60 + 30, 14 * 60, 15 * 60)


def is_60m_end(klu_15m: CKLine_Unit) -> bool:
    """该15M K线是否是一根完整60M K线的最后一根（按A股交易时段切分）"""
    return klu_15m.time.hour * 60 + klu_15m.time.minute in A_SHARE_60M_END_MINUTES


def is_same_day(t1, t2) -> bool:
    return (t1.year, t1.month, t1.day) == (t2.year, t2.month, t2.day)


def combine_60m_klu_form_15m(klu_15m_lst: List[CKLine_Unit]) -> CKLine_Unit:
    """把同一根60M周期内的15M K线合成一根60M K线"""
    kl_dict = {
        DATA_FIELD.FIELD_TIME: klu_15m_lst[-1].time,
        DATA_FIELD.FIELD_OPEN: klu_15m_lst[0].open,
        DATA_FIELD.FIELD_CLOSE: klu_15m_lst[-1].close,
        DATA_FIELD.FIELD_HIGH: max(klu.high for klu in klu_15m_lst),
        DATA_FIELD.FIELD_LOW: min(klu.low for klu in klu_15m_lst),
    }
    # 补聚合交易量字段，保证合成K线的 trade_info 不丢 volume/turnover
    for metric in (DATA_FIELD.FIELD_VOLUME, DATA_FIELD.FIELD_TURNOVER):
        values = [klu.trade_info.metric.get(metric) for klu in klu_15m_lst]
        values = [v for v in values if v is not None]
        if values:
            kl_dict[metric] = sum(values)
    return CKLine_Unit(kl_dict)


if __name__ == "__main__":
    """
    演示如何让"小级别K线更新"实时刷新CChan的计算结果：
    每来一根15M K线，就重新合成当前这根未完结的60M K线，
    在快照的deepcopy副本上重放，拿到最新的缠论结构。

    trigger_load契约：同一级别内K线时间必须严格递增，不能覆写已喂入的K线。
    因此"更新进行中的60M K线"只能借助快照重放实现：
    - chan_snapshot只固化已完结的60M K线；
    - 进行中那根每次都整组重新喂给全新的副本（对副本而言这些K线尚未被
      消费过，不会违反单调性）。

    15M->60M的分组必须按真实交易时段切分且不能跨交易日，否则跨天的15M
    子线挂到次日60M父线上会触发 KL_TIME_INCONSISTENT。
    """
    code = "sz.000001"
    begin_time = "2023-09-10"
    end_time = None
    data_src_type = DATA_SRC.BAO_STOCK
    lv_list = [KL_TYPE.K_60M, KL_TYPE.K_15M]

    config = CChanConfig({
        "trigger_step": True,
    })

    # 快照：只包含已完结的60M K线
    chan_snapshot = CChan(
        code=code,
        data_src=data_src_type,
        lv_list=lv_list,
        config=config,
    )
    CBaoStock.do_init()
    data_src = CBaoStock(code, k_type=KL_TYPE.K_15M, begin_date=begin_time, end_date=end_time, autype=AUTYPE.QFQ)  # 获取最小级别

    klu_15m_lst_tmp: List[CKLine_Unit] = []  # 存储用于合成当前60M K线的15M K线

    def replay_tmp_klus() -> CChan:
        """把当前进行中的60M K线重放进快照副本，返回算好的CChan"""
        chan: CChan = copy.deepcopy(chan_snapshot)
        # 如果是用序列化方式，这里可以采用pickle.load()
        chan.trigger_load({
            KL_TYPE.K_60M: [combine_60m_klu_form_15m(klu_15m_lst_tmp)],
            KL_TYPE.K_15M: list(klu_15m_lst_tmp),
        })
        return chan

    for klu_15m in data_src.get_kl_data():  # 获取单根15分钟K线
        if klu_15m_lst_tmp and not is_same_day(klu_15m.time, klu_15m_lst_tmp[0].time):
            # 跨交易日：上一交易日的15M缓存先结算成一根（可能不完整的）60M K线
            # 固化进快照，避免跨天子K线挂到次日父K线触发 KL_TIME_INCONSISTENT
            chan_snapshot = replay_tmp_klus()
            klu_15m_lst_tmp = []

        klu_15m_lst_tmp.append(klu_15m)
        chan = replay_tmp_klus()

        """
        策略开始：
        这里基于chan实现你的策略
        """
        for kl_type, ele_manager in chan.kl_datas.items():
            # 打印当前每一级别分别有多少K线
            print(klu_15m.time, kl_type, sum(len(klc) for klc in ele_manager))
        # 策略结束：

        if is_60m_end(klu_15m):  # 收齐该60M时段最后一根15M，这根60M K线将来不会再变化
            """
            把当前完整chan重新保存成chan_snapshot
            如果是序列化方式，这里可以采用pickle.dump()
            """
            chan_snapshot = chan
            klu_15m_lst_tmp = []  # 清空15M K线缓存，用于下一个60M周期的合成

    CBaoStock.do_close()
