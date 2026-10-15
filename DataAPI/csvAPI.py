import datetime
import os
import re

from Common.CEnum import DATA_FIELD, KL_TYPE
from Common.ChanException import CChanException, ErrCode
from Common.CTime import CTime
from KLine.KLine_Unit import CKLine_Unit

from .CommonStockAPI import CCommonStockApi, create_item_dict, parse_time_column

# code whitelist: "<market>.<code>" (e.g. sh.600000/sz.399001/bj.430047) or bare alphanumeric
_SAFE_CODE_PATTERN = re.compile(r"^[a-zA-Z0-9]+(\.[a-zA-Z0-9]+)?$")

# begin/end_date 容忍的输入格式；(fmt, 是否纯日期粒度)
_BOUND_FORMATS = (
    ("%Y-%m-%d %H:%M:%S", False),
    ("%Y-%m-%d %H:%M", False),
    ("%Y-%m-%d", True),
    ("%Y/%m/%d", True),
    ("%Y%m%d", True),
)


def _parse_bound(raw, is_end):
    """把 begin_date/end_date 解析为 CTime 边界，容忍多种输入格式。

    纯日期粒度（如 ``2021-09-02``/``20210902``）：begin 取当日 00:00，end 取
    当日结束（auto=True -> ts 视作 23:59），保证 end_date 当天的数据仍被包含；
    显式带时刻的边界（含 17/19 位行内时间格式）按字面时刻比较。
    返回 None 表示未设置边界。
    """
    s = str(raw or "").strip()
    if not s:
        return None
    if len(s) in (17, 19):
        try:
            return parse_time_column(s, src_name="csv")
        except CChanException:
            pass  # 落到 strptime 兜底，仍失败则报 PARA_ERROR
    for fmt, day_only in _BOUND_FORMATS:
        try:
            dt = datetime.datetime.strptime(s, fmt)
        except ValueError:
            continue
        return CTime(dt.year, dt.month, dt.day, dt.hour, dt.minute, auto=(is_end and day_only))
    raise CChanException(f"csv 无法解析{'end' if is_end else 'begin'}_date: {raw!r}", ErrCode.PARA_ERROR)


def _resolve_encoding(file_path):
    # CN Windows/Excel 导出的 csv 常见 GBK；纯数字内容在两种编码下解码结果一致，
    # 有歧义的只是中文表头等非 ASCII 内容 —— 先全文试解 utf-8(-sig 顺带剥 BOM)，
    # 失败回退 gbk。探测多花一次全文 IO，避免迭代中途才炸解码错误。
    for enc in ("utf-8-sig", "gbk"):
        try:
            with open(file_path, "r", encoding=enc) as f:
                for _ in f:
                    pass
            return enc
        except UnicodeDecodeError:
            continue
    return "utf-8-sig"  # 保底：让正式读取抛出明确的解码错误


class CSV_API(CCommonStockApi):
    def __init__(self, code, k_type=KL_TYPE.K_DAY, begin_date=None, end_date=None, autype=None, asset_type="STOCK"):
        if not _SAFE_CODE_PATTERN.match(str(code or "")):
            raise CChanException(f"invalid csv code: {code!r}", ErrCode.PARA_ERROR)
        self.headers_exist = True  # 第一行是否是标题，如果是数据，设置为False
        self.columns = [
            DATA_FIELD.FIELD_TIME,
            DATA_FIELD.FIELD_OPEN,
            DATA_FIELD.FIELD_HIGH,
            DATA_FIELD.FIELD_LOW,
            DATA_FIELD.FIELD_CLOSE,
            # DATA_FIELD.FIELD_VOLUME,
            # DATA_FIELD.FIELD_TURNOVER,
            # DATA_FIELD.FIELD_TURNRATE,
        ]  # 每一列字段
        self.time_column_idx = self.columns.index(DATA_FIELD.FIELD_TIME)
        super(CSV_API, self).__init__(code, k_type, begin_date, end_date, autype, asset_type)

    def _resolve_file_path(self):
        cur_path = os.path.dirname(os.path.realpath(__file__))
        data_dir = os.path.realpath(os.path.join(cur_path, ".."))
        k_type = self.k_type.name[2:].lower()
        file_path = os.path.realpath(os.path.join(data_dir, f"{self.code}_{k_type}.csv"))
        try:
            in_data_dir = os.path.commonpath([data_dir, file_path]) == data_dir
        except ValueError:
            in_data_dir = False
        if not in_data_dir:
            raise CChanException(f"csv file path escapes data dir: {file_path}", ErrCode.PARA_ERROR)
        return file_path

    def get_kl_data(self):
        file_path = self._resolve_file_path()
        if not os.path.exists(file_path):
            raise CChanException(f"file not exist: {file_path}", ErrCode.SRC_DATA_NOT_FOUND)

        # 边界先解析成 CTime 再比较；原始字符串字典序比较对不同格式会整体反转
        # （如行时间 '20210902113000000' 与边界 '2021-09-02' 在第5位 '0'>'-'）
        begin_t = _parse_bound(self.begin_date, is_end=False)
        end_t = _parse_bound(self.end_date, is_end=True)

        with open(file_path, "r", encoding=_resolve_encoding(file_path)) as f:
            for line_number, line in enumerate(f):
                if line_number == 0 and self.headers_exist:
                    # 表头也校验列数，提前暴露读错文件/列错位
                    if len(line.rstrip("\r\n").split(",")) != len(self.columns):
                        raise CChanException(
                            f"file header column count mismatch: {file_path}",
                            ErrCode.SRC_DATA_FORMAT_ERROR)
                    continue
                if not line.strip():
                    continue  # 跳过空行（常见于文件末尾多出的换行）
                data = line.rstrip("\r\n").split(",")  # rstrip 清掉 \r\n 残留
                if len(data) != len(self.columns):
                    raise CChanException(
                        f"file format error at line {line_number + 1}: {file_path}",
                        ErrCode.SRC_DATA_FORMAT_ERROR)
                try:
                    row_time = parse_time_column(data[self.time_column_idx], src_name="csv")
                except CChanException as e:
                    raise CChanException(
                        f"{file_path} line {line_number + 1}: {e.msg}", e.errcode) from e
                if begin_t is not None and row_time < begin_t:
                    continue
                if end_t is not None and row_time > end_t:
                    continue
                yield CKLine_Unit(create_item_dict(data, self.columns, src_name="csv"))

    def SetBasciInfo(self):
        pass

    @classmethod
    def do_init(cls):
        pass

    @classmethod
    def do_close(cls):
        pass
