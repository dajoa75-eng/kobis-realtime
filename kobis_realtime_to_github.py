from __future__ import annotations

import csv
import json
import re
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

PC_URL = "https://www.kobis.or.kr/kobis/business/stat/boxs/findRealTicketList.do"
MOBILE_URL = "https://kobis.or.kr/kobis/mobile/main/findRealTicketList.do"
KST = ZoneInfo("Asia/Seoul")
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)


@dataclass
class TicketRow:
    collected_at: str
    source: str
    rank: int | None
    movie_name_ko: str
    movie_name_en: str | None
    reservation_rate: str | None
    reservation_audience: int | None
    reservation_sales: int | None = None
    raw_text: str | None = None


def clean(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def parse_int(text: str | None) -> int | None:
    if not text:
        return None
    digits = re.sub(r"[^0-9]", "", text)
    return int(digits) if digits else None


def parse_rate(text: str | None) -> str | None:
    if not text:
        return None
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*%", text)
    return f"{m.group(1)}%" if m else None


def split_title(text: str) -> tuple[str, str | None]:
    text = clean(text)
    # 예: 군체 (COLONY)
    m = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", text)
    if m:
        return clean(m.group(1)), clean(m.group(2)) or None
    return text, None


def fetch_mobile() -> list[TicketRow]:
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    }
    res = requests.get(MOBILE_URL, headers=headers, timeout=30)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "html.parser")
    collected_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S %Z")

    text = soup.get_text("\n")
    lines = [clean(x) for x in text.splitlines() if clean(x)]

    rows: list[TicketRow] = []
    # 모바일 HTML은 순위 숫자 → 영화명 → 예매율(예매관객수) → 47.0% (107,175명) 구조로 노출된다.
    for i, line in enumerate(lines):
        if not re.fullmatch(r"\d{1,3}", line):
            continue
        rank = int(line)
        if rank < 1 or rank > 9999:
            continue
        title = lines[i + 1] if i + 1 < len(lines) else ""
        metric = ""
        for j in range(i + 2, min(i + 8, len(lines))):
            if "%" in lines[j] and "명" in lines[j]:
                metric = lines[j]
                break
        if not title or not metric:
            continue
        ko, en = split_title(title)
        rows.append(
            TicketRow(
                collected_at=collected_at,
                source=MOBILE_URL,
                rank=rank,
                movie_name_ko=ko,
                movie_name_en=en,
                reservation_rate=parse_rate(metric),
                reservation_audience=parse_int(metric.split("(")[-1]),
                raw_text=metric,
            )
        )

    # 중복 제거
    dedup: dict[int, TicketRow] = {}
    for row in rows:
        if row.rank is not None and row.rank not in dedup:
            dedup[row.rank] = row
    return [dedup[k] for k in sorted(dedup)]


def fetch_pc_with_playwright() -> list[TicketRow]:
    """PC 페이지 렌더링 뒤 표를 파싱한다. 실패하면 호출부에서 모바일로 대체한다."""
    from playwright.sync_api import sync_playwright

    collected_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S %Z")
    rows: list[TicketRow] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(locale="ko-KR")
        page.goto(PC_URL, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(3000)
        table_rows = page.locator("table tbody tr").all()
        for tr in table_rows:
            cells = [clean(c.inner_text()) for c in tr.locator("td").all()]
            if len(cells) < 3:
                continue
            # KOBIS PC 표 컬럼은 개편될 수 있어, 숫자·%·명·원 텍스트를 유연하게 잡는다.
            rank = parse_int(cells[0])
            movie = next((c for c in cells[1:4] if c and not re.fullmatch(r"[0-9,\.\-%원명]+", c)), "")
            rate = next((parse_rate(c) for c in cells if "%" in c), None)
            audience = next((parse_int(c) for c in cells if "명" in c), None)
            sales = next((parse_int(c) for c in cells if "원" in c), None)
            if not movie or not rate:
                continue
            ko, en = split_title(movie)
            rows.append(
                TicketRow(
                    collected_at=collected_at,
                    source=PC_URL,
                    rank=rank,
                    movie_name_ko=ko,
                    movie_name_en=en,
                    reservation_rate=rate,
                    reservation_audience=audience,
                    reservation_sales=sales,
                    raw_text=" | ".join(cells),
                )
            )
        browser.close()
    return rows


def write_files(rows: list[TicketRow]) -> tuple[Path, Path, Path]:
    now = datetime.now(KST)
    stamp = now.strftime("%Y-%m-%d_%H%M")
    csv_path = DATA_DIR / f"kobis_realtime_{stamp}.csv"
    json_path = DATA_DIR / f"kobis_realtime_{stamp}.json"
    latest_path = DATA_DIR / "latest.json"

    payload = [asdict(r) for r in rows]
    fieldnames = list(payload[0].keys()) if payload else list(TicketRow.__dataclass_fields__.keys())
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(payload)
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    with latest_path.open("w", encoding="utf-8") as f:
        json.dump({"updated_at": now.strftime("%Y-%m-%d %H:%M:%S %Z"), "rows": payload}, f, ensure_ascii=False, indent=2)
    return csv_path, json_path, latest_path


def main() -> int:
    rows: list[TicketRow] = []
    errors: list[str] = []

    try:
        rows = fetch_pc_with_playwright()
    except Exception as e:
        errors.append(f"PC fetch failed: {e}")

    if not rows:
        try:
            rows = fetch_mobile()
        except Exception as e:
            errors.append(f"Mobile fetch failed: {e}")

    log_path = DATA_DIR / "last_run_log.txt"
    if not rows:
        log_path.write_text("\n".join(errors), encoding="utf-8")
        print("수집 실패")
        print("\n".join(errors))
        return 1

    csv_path, json_path, latest_path = write_files(rows)
    log_path.write_text(f"OK\nrows={len(rows)}\n{csv_path.name}\n{json_path.name}\n" + "\n".join(errors), encoding="utf-8")
    print(f"저장 완료: {csv_path}, {json_path}, {latest_path}")
    print(f"수집 건수: {len(rows)}")
    if errors:
        print("참고:")
        print("\n".join(errors))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
