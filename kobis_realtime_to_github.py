from __future__ import annotations

import csv
import json
import re
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
    m = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", text)
    if m:
        return clean(m.group(1)), clean(m.group(2)) or None
    return text, None


def rows_from_text(text: str, source: str, collected_at: str) -> list[TicketRow]:
    lines = [clean(x) for x in text.splitlines() if clean(x)]
    rows: list[TicketRow] = []

    for i, line in enumerate(lines):
        if not re.fullmatch(r"\d{1,4}", line):
            continue

        rank = int(line)
        if rank < 1 or rank > 9999:
            continue

        title = lines[i + 1] if i + 1 < len(lines) else ""
        metric = ""

        for j in range(i + 2, min(i + 12, len(lines))):
            if "%" in lines[j] and "명" in lines[j]:
                metric = lines[j]
                break

        if not title or not metric:
            continue

        ko, en = split_title(title)
        rows.append(
            TicketRow(
                collected_at=collected_at,
                source=source,
                rank=rank,
                movie_name_ko=ko,
                movie_name_en=en,
                reservation_rate=parse_rate(metric),
                reservation_audience=parse_int(metric.split("(")[-1]),
                raw_text=metric,
            )
        )

    dedup: dict[int, TicketRow] = {}
    for row in rows:
        if row.rank is not None and row.rank not in dedup:
            dedup[row.rank] = row

    return [dedup[k] for k in sorted(dedup)]


def fetch_mobile_static() -> list[TicketRow]:
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    }
    res = requests.get(MOBILE_URL, headers=headers, timeout=30)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "html.parser")
    collected_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S %Z")
    return rows_from_text(soup.get_text("\n"), MOBILE_URL, collected_at)


def fetch_mobile_with_playwright() -> list[TicketRow]:
    """모바일 페이지에서 '전체보기'를 눌러 펼쳐진 목록을 수집한다."""
    from playwright.sync_api import sync_playwright

    collected_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S %Z")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            locale="ko-KR",
            viewport={"width": 390, "height": 1600},
            user_agent=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
            ),
        )
        page.goto(MOBILE_URL, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(1500)

        # 모바일 화면의 전체보기/더보기/펼쳐보기 버튼을 가능한 범위에서 누른다.
        for label in ["전체보기", "더보기", "전체 보기", "more", "MORE"]:
            try:
                loc = page.get_by_text(label, exact=False)
                count = loc.count()
                for idx in range(min(count, 5)):
                    try:
                        loc.nth(idx).click(timeout=3000)
                        page.wait_for_timeout(1200)
                    except Exception:
                        pass
            except Exception:
                pass

        # 스크롤로 추가 로딩이 붙는 경우 대비
        previous_height = 0
        for _ in range(12):
            height = page.evaluate("document.body.scrollHeight")
            if height == previous_height:
                break
            previous_height = height
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(800)

        text = page.locator("body").inner_text(timeout=10000)
        browser.close()

    return rows_from_text(text, MOBILE_URL, collected_at)


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

    dedup: dict[int, TicketRow] = {}
    for row in rows:
        if row.rank is not None and row.rank not in dedup:
            dedup[row.rank] = row
    return [dedup[k] for k in sorted(dedup)]


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
        json.dump(
            {"updated_at": now.strftime("%Y-%m-%d %H:%M:%S %Z"), "rows": payload},
            f,
            ensure_ascii=False,
            indent=2,
        )

    return csv_path, json_path, latest_path


def main() -> int:
    rows: list[TicketRow] = []
    errors: list[str] = []

    # 1순위: 모바일 전체보기
    try:
        rows = fetch_mobile_with_playwright()
    except Exception as e:
        errors.append(f"Mobile Playwright fetch failed: {e}")

    # 2순위: PC 페이지
    if not rows:
        try:
            rows = fetch_pc_with_playwright()
        except Exception as e:
            errors.append(f"PC fetch failed: {e}")

    # 3순위: 모바일 기본 화면(TOP5)
    if not rows:
        try:
            rows = fetch_mobile_static()
        except Exception as e:
            errors.append(f"Mobile static fetch failed: {e}")

    log_path = DATA_DIR / "last_run_log.txt"

    if not rows:
        log_path.write_text("\n".join(errors), encoding="utf-8")
        print("수집 실패")
        print("\n".join(errors))
        return 1

    csv_path, json_path, latest_path = write_files(rows)
    log_path.write_text(
        f"OK\nrows={len(rows)}\n{csv_path.name}\n{json_path.name}\n" + "\n".join(errors),
        encoding="utf-8",
    )

    print(f"저장 완료: {csv_path}, {json_path}, {latest_path}")
    print(f"수집 건수: {len(rows)}")
    if errors:
        print("참고:")
        print("\n".join(errors))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
