from __future__ import annotations

import argparse
import json
import re
import warnings
from dataclasses import asdict, dataclass
from io import BytesIO, StringIO
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests
from bs4 import BeautifulSoup
from requests.exceptions import SSLError


DEFAULT_SOURCE_MAP = {
    "0050": "https://www.yuantaetfs.com/product/detail/0050/ratio",
    "0052": "https://www.fubon.com/asset-management/fund/basic-information/etf/0052",
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/137.0.0.0 Safari/537.36"
)

CODE_PATTERN = re.compile(r"(?P<code>\d{4})(?:\s+|[-:：/\\])?(?P<name>.*)")
WEIGHT_HINTS = ("權重", "比重", "比例", "持股比率", "持股權重", "weight")
CODE_HINTS = ("代號", "證券代號", "股票代號", "成分股代號", "stock id", "ticker", "symbol")
NAME_HINTS = ("名稱", "證券名稱", "股票名稱", "成分股名稱", "company", "stock")


@dataclass
class ConstituentRow:
    code: str
    name: str | None
    weight: float | None
    source: str
    etf: str

    @property
    def yf_symbol(self) -> str:
        return f"{self.code}.TW"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="臨時整理 0050 / 0052 成分股，可吃官方或券商 URL、HTML、CSV、XLSX 檔案。"
    )
    parser.add_argument(
        "--source",
        action="append",
        default=[],
        metavar="ETF=URL_OR_PATH",
        help="指定來源，例如 0052=C:/temp/0052.xlsx 或 0050=https://...",
    )
    parser.add_argument(
        "--etf",
        nargs="*",
        default=["0050", "0052"],
        help="要整理的 ETF 代碼，預設為 0050 0052",
    )
    parser.add_argument(
        "--json",
        dest="json_path",
        help="將結果輸出成 JSON 檔",
    )
    parser.add_argument(
        "--codes-only",
        action="store_true",
        help="只輸出代號清單，方便直接貼回程式",
    )
    parser.add_argument(
        "--with-suffix",
        action="store_true",
        help="輸出時附上 .TW 後綴",
    )
    parser.add_argument(
        "--allow-text-fallback",
        action="store_true",
        help="當來源不是表格格式時，允許從全文鬆散擷取 4 位數代號；預設關閉以避免抓錯。",
    )
    return parser.parse_args()


def build_source_map(items: list[str], etfs: list[str]) -> dict[str, str]:
    mapping = {etf: DEFAULT_SOURCE_MAP.get(etf, "") for etf in etfs}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"source 格式錯誤：{item}，應為 ETF=URL_OR_PATH")
        etf, raw_source = item.split("=", 1)
        mapping[etf.strip()] = raw_source.strip()
    return mapping


def is_url(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://")


def fetch_bytes(source: str) -> bytes:
    if is_url(source):
        try:
            response = requests.get(source, timeout=30, headers={"User-Agent": USER_AGENT})
        except SSLError:
            warnings.filterwarnings("ignore", message="Unverified HTTPS request")
            response = requests.get(source, timeout=30, headers={"User-Agent": USER_AGENT}, verify=False)
        response.raise_for_status()
        return response.content

    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"找不到來源：{source}")
    return path.read_bytes()


def fetch_text(source: str) -> str:
    payload = fetch_bytes(source)
    for encoding in ("utf-8", "utf-8-sig", "big5", "cp950", "latin-1"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="ignore")


def read_tables_from_source(source: str) -> list[pd.DataFrame]:
    lowered = source.lower()
    payload = fetch_bytes(source)

    if lowered.endswith((".xlsx", ".xls")):
        return pd.read_excel(BytesIO(payload), sheet_name=None).values()

    if lowered.endswith(".csv"):
        return [pd.read_csv(BytesIO(payload))]

    if lowered.endswith(".tsv") or lowered.endswith(".txt"):
        return [pd.read_csv(BytesIO(payload), sep="\t")]

    html = fetch_text(source)
    return parse_html_tables(html)


def parse_html_tables(html: str) -> list[pd.DataFrame]:
    soup = BeautifulSoup(html, "html.parser")
    tables: list[pd.DataFrame] = []

    for table in soup.find_all("table"):
        rows: list[list[str]] = []
        for tr in table.find_all("tr"):
            cells = tr.find_all(["th", "td"])
            if not cells:
                continue
            rows.append([" ".join(cell.get_text(" ", strip=True).split()) for cell in cells])

        if len(rows) < 2:
            continue

        header = rows[0]
        body = rows[1:]
        width = max(len(header), *(len(row) for row in body))
        padded_header = header + [f"col_{index}" for index in range(len(header), width)]
        padded_body = [row + [""] * (width - len(row)) for row in body]
        tables.append(pd.DataFrame(padded_body, columns=padded_header[:width]))

    return tables


def normalize_header(value: object) -> str:
    text = " ".join(str(value).strip().split())
    return text.lower()


def find_column(columns: Iterable[object], hints: tuple[str, ...]) -> object | None:
    normalized_map = {column: normalize_header(column) for column in columns}
    for hint in hints:
        hint_lower = hint.lower()
        for column, normalized in normalized_map.items():
            if hint_lower in normalized:
                return column
    return None


def parse_weight(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("%", "")
    if not text or text.lower() in {"nan", "none"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def split_code_and_name(raw_value: object) -> tuple[str | None, str | None]:
    text = " ".join(str(raw_value or "").strip().split())
    if not text or text.lower() == "nan":
        return None, None

    match = CODE_PATTERN.search(text)
    if not match:
        return None, text

    code = match.group("code")
    name = match.group("name").strip() or None
    return code, name


def extract_rows_from_table(df: pd.DataFrame, etf: str, source: str) -> list[ConstituentRow]:
    if df.empty:
        return []

    working_df = df.copy()
    working_df.columns = [str(column).strip() for column in working_df.columns]

    code_col = find_column(working_df.columns, CODE_HINTS)
    name_col = find_column(working_df.columns, NAME_HINTS)
    weight_col = find_column(working_df.columns, WEIGHT_HINTS)

    if code_col is None:
        for column in working_df.columns:
            sample = " ".join(working_df[column].astype(str).head(10).tolist())
            if re.search(r"\b\d{4}\b", sample):
                code_col = column
                break

    if code_col is None:
        return []

    extracted: list[ConstituentRow] = []
    seen_codes: set[str] = set()

    for _, row in working_df.iterrows():
        code, derived_name = split_code_and_name(row.get(code_col))
        if code is None and name_col is not None:
            code, derived_name = split_code_and_name(row.get(name_col))

        if code is None or not re.fullmatch(r"\d{4}", code):
            continue

        name = derived_name
        if name_col is not None:
            candidate_name = str(row.get(name_col) or "").strip()
            if candidate_name and candidate_name.lower() != "nan" and not re.fullmatch(r"\d{4}", candidate_name):
                name = candidate_name

        if code in seen_codes:
            continue

        seen_codes.add(code)
        extracted.append(
            ConstituentRow(
                code=code,
                name=name,
                weight=parse_weight(row.get(weight_col)) if weight_col is not None else None,
                source=source,
                etf=etf,
            )
        )

    return extracted


def extract_rows(etf: str, source: str, allow_text_fallback: bool = False) -> list[ConstituentRow]:
    tables = list(read_tables_from_source(source))
    all_rows: list[ConstituentRow] = []
    for table in tables:
        all_rows.extend(extract_rows_from_table(table, etf=etf, source=source))

    if all_rows:
        all_rows.sort(key=lambda item: (item.weight is None, -(item.weight or 0), item.code))
        return dedupe_rows(all_rows)

    if not allow_text_fallback:
        return []

    text = fetch_text(source)
    fallback_rows = extract_rows_from_text(etf=etf, source=source, text=text)
    return dedupe_rows(fallback_rows)


def extract_rows_from_text(etf: str, source: str, text: str) -> list[ConstituentRow]:
    rows: list[ConstituentRow] = []
    seen_codes: set[str] = set()
    for line in text.splitlines():
        code, name = split_code_and_name(line)
        if code is None or code in seen_codes or not re.fullmatch(r"\d{4}", code):
            continue
        seen_codes.add(code)
        rows.append(ConstituentRow(code=code, name=name, weight=None, source=source, etf=etf))
    return rows


def dedupe_rows(rows: list[ConstituentRow]) -> list[ConstituentRow]:
    deduped: dict[str, ConstituentRow] = {}
    for row in rows:
        if row.code not in deduped:
            deduped[row.code] = row
    return list(deduped.values())


def print_rows(etf: str, rows: list[ConstituentRow], codes_only: bool, with_suffix: bool) -> None:
    print(f"\n[{etf}] constituents={len(rows)}")
    if not rows:
        print("  no rows parsed")
        return

    for row in rows:
        display_code = row.yf_symbol if with_suffix else row.code
        if codes_only:
            print(display_code)
            continue

        if row.weight is None:
            print(f"  {display_code:10} {row.name or ''}")
        else:
            print(f"  {display_code:10} {row.name or '':20} {row.weight:>8.4f}%")


def print_union(result_map: dict[str, list[ConstituentRow]], with_suffix: bool) -> None:
    union_codes = sorted({row.code for rows in result_map.values() for row in rows})
    print(f"\n[UNION] listed constituents={len(union_codes)}")
    for code in union_codes:
        print(f"  {code}.TW" if with_suffix else f"  {code}")


def write_json(json_path: str, result_map: dict[str, list[ConstituentRow]]) -> None:
    payload = {
        etf: {
            "count": len(rows),
            "constituents": [asdict(row) | {"yf_symbol": row.yf_symbol} for row in rows],
        }
        for etf, rows in result_map.items()
    }
    payload["union_codes"] = sorted({row.code for rows in result_map.values() for row in rows})

    target = Path(json_path)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nJSON written to {target}")


def main() -> None:
    args = parse_args()
    source_map = build_source_map(args.source, args.etf)

    result_map: dict[str, list[ConstituentRow]] = {}

    for etf in args.etf:
        source = source_map.get(etf, "").strip()
        if not source:
            print(f"[Warn] {etf} 沒有可用來源，請用 --source {etf}=URL_OR_PATH 指定")
            result_map[etf] = []
            continue

        try:
            rows = extract_rows(etf=etf, source=source, allow_text_fallback=args.allow_text_fallback)
        except Exception as exc:
            print(f"[Warn] {etf} 抓取失敗：{exc}")
            rows = []

        result_map[etf] = rows
        if not rows:
            print(f"[Hint] {etf} 若官網是動態頁，請改用下載後的 CSV/XLSX/HTML 檔，或可直接指定另一個有表格的券商 URL。")
        print_rows(etf=etf, rows=rows, codes_only=args.codes_only, with_suffix=args.with_suffix)

    print_union(result_map, with_suffix=args.with_suffix)

    if args.json_path:
        write_json(args.json_path, result_map)


if __name__ == "__main__":
    main()