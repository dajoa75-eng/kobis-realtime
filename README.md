# KOBIS 실시간 예매율 매일 0시 자동 저장

GitHub Actions가 매일 한국 시간 0시에 KOBIS 실시간 예매율 데이터를 수집하고, `data` 폴더에 CSV와 JSON으로 저장한다.

## 저장 파일

- `data/kobis_realtime_YYYY-MM-DD_0000.csv`
- `data/kobis_realtime_YYYY-MM-DD_0000.json`
- `data/latest.json` : 가장 최근 실행 결과
- `data/last_run_log.txt` : 마지막 실행 로그

## 설치 방법

1. GitHub에서 새 저장소를 만든다.
2. 이 압축 파일 안의 모든 파일과 폴더를 그대로 업로드한다.
3. 저장소 상단의 `Actions` 탭을 누른다.
4. GitHub Actions 사용을 허용한다.
5. 왼쪽에서 `Save KOBIS realtime ticket data`를 누른다.
6. `Run workflow`를 눌러 한 번 수동 실행한다.
7. 성공하면 이후 매일 한국 시간 0시에 자동 저장된다.

## 중요 설정

저장소 `Settings` → `Actions` → `General`에서 아래 설정을 확인한다.

- Workflow permissions: `Read and write permissions`

이 설정이 꺼져 있으면 데이터 파일을 저장소에 다시 커밋하지 못한다.

## 참고

- GitHub Actions 예약 실행은 약간 늦게 시작될 수 있다.
- 0시 정각의 완전한 동시 캡처는 보장하기 어렵다.
- KOBIS PC 페이지 수집이 실패하면 모바일 실시간 예매율 페이지 기준으로 저장한다.
