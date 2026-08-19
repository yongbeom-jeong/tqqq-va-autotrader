# tqqq-va-autotrader

Toss Securities Open API를 사용하는 TQQQ Value Averaging 자동매매 프로젝트입니다.
현재 1단계는 주문을 전혀 생성하지 않는 read-only `status` 명령을 제공합니다.
`dry-run` 명령은 CSV 경로를 기준으로 TQQQ/SGOV 리밸런싱 계획만 계산합니다.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
Copy-Item .env.example .env
```

`.env`에 `TOSS_CLIENT_ID`, `TOSS_CLIENT_SECRET`을 입력하세요. 계좌가 여러 개라면
`TOSS_ACCOUNT_SEQ`도 지정할 수 있습니다. 호출 PC의 공인 IP는 Toss WTS의 Open API
허용 IP 목록에 등록되어 있어야 합니다.

기본 VA 경로는 `data/va_path.csv`입니다. 다른 파일을 사용하려면 `.env`의
`VA_PATH_FILE`을 변경하세요. 날짜는 한국시간 기준 `YYYY-MM-DD`, 금액은 `$`와
천 단위 쉼표가 없는 USD 숫자로 입력합니다.

## VA 경로 CSV 유지보수

앞으로 정기적으로 갱신해야 하는 파일은 기본적으로 `data/va_path.csv`입니다.
현재 데이터의 마지막 날짜가 지나기 전에 다음 달 경로를 추가합니다. 미국장
휴장일이나 주말에도 프로그램은 매시간 실행되므로 날짜가 비지 않도록 달력의 모든
날짜를 넣는 것을 권장합니다.

첫 줄과 열 순서는 다음 형식을 그대로 유지합니다.

```csv
date,target_value,lower_band,upper_band
YYYY-MM-DD,목표가,밴드하단,밴드상단
```

두 번째 줄은 형식 설명용이며 실제 파일에는 엑셀에서 확인한 숫자를 입력합니다.

- `date`: 한국시간 기준 날짜, `YYYY-MM-DD`
- `target_value`: 해당 날짜의 목표 가치
- `lower_band`: 매수 판단에 사용하는 밴드 하단
- `upper_band`: 매도 판단에 사용하는 밴드 상단
- 금액: USD 숫자만 입력하며 `$`와 천 단위 쉼표는 넣지 않음
- 각 날짜는 한 번만 입력하며 `0 < lower_band < target_value < upper_band`를 만족해야 함

엑셀에서 복사할 때는 수식 자체가 아니라 계산된 값을 소수점 둘째 자리까지
입력합니다. 기존 마지막 행 아래에 날짜순으로 추가하고 헤더를 다시 붙이지 않습니다.
갱신 후 Windows 개발 PC에서 다음 명령으로 변경 내용과 테스트를 확인합니다.

```powershell
git diff -- data/va_path.csv
python -m pytest
```

문제가 없으면 CSV만 커밋하고 GitHub에 올립니다.

```powershell
git add data/va_path.csv
git commit -m "data: add October VA path"
git push origin main
```

실전 운용 중인 Raspberry Pi에서는 갱신 도중 한 사이클이 시작되지 않도록 타이머를
잠시 멈추고 적용합니다.

```powershell
ssh jyb@192.168.0.102
```

접속한 Raspberry Pi에서 다음 명령을 실행합니다.

```sh
sudo systemctl stop tqqq-va-autotrader.timer
cd ~/tqqq-va-autotrader
git pull
tail data/va_path.csv
sudo systemctl start tqqq-va-autotrader.timer
systemctl list-timers --no-pager tqqq-va-autotrader.timer
```

`data/` 디렉터리는 컨테이너에 bind mount되어 있으므로 CSV만 변경했을 때는 Docker
이미지를 다시 빌드하거나 systemd 서비스를 다시 설치할 필요가 없습니다. 코드나
의존성, `Dockerfile`이 변경된 경우에만 별도로 이미지를 다시 빌드합니다.

## Status

```powershell
python main.py status
```

OAuth 인증 후 계좌, 미국장 세션, TQQQ 현재가/호가/보유량, USD 매수가능금액,
TQQQ 미체결 주문을 조회합니다. API 오류에는 상태 코드, 오류 코드와 requestId를
표시하지만 client secret, access token, Authorization 헤더는 출력하지 않습니다.

## Dry run

```powershell
python main.py dry-run
```

오늘 TQQQ 가치가 밴드 안이면 `HOLD`입니다. 하단 밖이면 밴드에 진입할 TQQQ
수량을 올림 계산하고, USD 현금이 부족하면 필요한 SGOV 매도 수량을 먼저
계획합니다. 상단 밖이면 TQQQ 매도 수량을 올림 계산한 뒤 예상 매도대금으로
매수 가능한 정수 SGOV 수량을 보여줍니다.

두 주문이 필요한 경우 두 번째 수량은 첫 주문 체결 후 반드시 보유량,
매수가능금액 및 시세를 다시 조회하여 재계산해야 합니다. 매도가능수량이 밴드
진입에 부족하거나 TQQQ/SGOV 미체결 주문이 있으면 fail closed로 중단합니다.
`dry-run` 자체는 조회와 계산만 하며 주문 생성 API를 호출하지 않습니다.

## Live execution

라이브 주문은 먼저 `dry-run` 결과를 확인한 뒤 `.env`에서 `DRY_RUN=false`로
변경해야 합니다. 환경 스위치와 별개로 `run` 명령을 직접 실행해야 주문이 시작됩니다.

```env
DRY_RUN=false
MAX_ORDER_AMOUNT_USD=5000
ORDER_FILL_TIMEOUT_SECONDS=60
ORDER_POLL_INTERVAL_SECONDS=2
```

```powershell
python main.py run
```

엔진은 지정가 정수 주문만 사용합니다. 한 번에 주문 하나만 제출하고, 첫 주문이
terminal 상태가 된 뒤 계좌와 시세를 다시 조회해 두 번째 주문을 계산합니다.
타임아웃이면 주문을 취소하고 원주문 상태가 확정될 때까지 신규 주문을 차단합니다.

실행 상태는 `data/execution_state.json`에 원자적으로 저장됩니다. 동시에 실행된 다른
프로세스는 lock 파일에 의해 차단됩니다. 상태가 `UNRESOLVED`이거나 lock 파일이
비정상 종료 후 남아 있다면 Toss 주문 목록과 주문 상세를 직접 확인하기 전에는
상태 또는 lock 파일을 삭제하면 안 됩니다.

`run`은 다음 조건에서 주문하지 않습니다.

- 미국장이 닫혀 있음
- TQQQ 또는 SGOV 미체결 주문이 이미 있음
- CSV에 오늘 날짜가 없거나 밴드가 잘못됨
- 밴드 진입에 필요한 매도가능수량이 부족함
- 개별 주문 예상금액이 `MAX_ORDER_AMOUNT_USD`를 초과함
- 이전 실행 상태가 불명확하거나 다른 실행 프로세스가 존재함

## Tests

```powershell
python -m pytest
```

테스트는 mock 응답만 사용하므로 Toss credential이나 네트워크가 필요하지 않습니다.

부팅 직후 LAN은 연결됐지만 DNS가 아직 준비되지 않은 경우를 위해 systemd 서비스는
`openapi.tossinvest.com` 이름 해석을 최대 5분간 기다린 후 컨테이너를 시작합니다.
preflight가 실패하면 애플리케이션이나 주문 API를 실행하지 않고 종료합니다.

## Raspberry Pi deployment

Raspberry Pi OS 64-bit와 Docker Engine + Docker Compose 플러그인을 권장합니다.
저장소를 Pi에 clone한 뒤 `.env`를 별도로 생성하고, Toss WTS 허용 IP에 Pi가
사용하는 공인 IP를 등록하세요. `.env`는 GitHub에 올리지 않습니다.

```sh
git clone https://github.com/yongbeom-jeong/tqqq-va-autotrader.git
cd tqqq-va-autotrader
cp .env.example .env
nano .env
```

먼저 이미지를 빌드하고 조회·dry-run을 검증합니다.

```sh
docker compose build trader
sh deploy/pi-check.sh
```

기본 권장 배포는 매시간 한 번 실행되는 shadow 모드입니다.

```sh
sudo sh deploy/install-systemd.sh dry-run
systemctl list-timers tqqq-va-autotrader.timer
journalctl -u tqqq-va-autotrader.service -n 100 --no-pager
```

즉시 한 사이클만 수동 실행하려면 다음 명령을 사용합니다.

```sh
sudo systemctl start tqqq-va-autotrader.service
journalctl -u tqqq-va-autotrader.service -n 100 --no-pager
```

며칠간 shadow 로그를 확인한 뒤 라이브로 전환할 때만 `.env`의 `DRY_RUN=false`를
설정하고 서비스를 다시 설치합니다.

```sh
sudo sh deploy/install-systemd.sh run
```

다시 shadow 모드로 잠그려면 `DRY_RUN=true`로 되돌린 후
`sudo sh deploy/install-systemd.sh dry-run`을 실행합니다. systemd 서비스는 Docker
컨테이너를 매번 새로 시작하지만 `data/`를 bind mount하므로 실행 상태와 CSV는
호스트에 유지됩니다.
