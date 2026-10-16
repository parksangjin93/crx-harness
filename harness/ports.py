"""
COM 포트 식별.

Windows 의 COM 번호는 재부팅·재연결마다 바뀐다. 4대를 돌리면
어제의 COM7 이 오늘의 COM7 이 아니고, 순서가 뒤바뀌어
crx1 결과가 trx1 에 찍힌다.

CP2108 같은 Quad 브리지는 Interface 0~3 이 같은 시리얼 번호를
공유하므로 SER 만으로는 구분되지 않는다. LOCATION 까지 봐야 한다.

    python -m harness.ports

를 돌려 나온 문자열을 config/duts.yaml 의 port_match 에 넣는다.
"""

from __future__ import annotations

from serial.tools import list_ports


class PortNotFound(Exception):
    pass


def scan() -> list[dict]:
    out = []
    for p in sorted(list_ports.comports(), key=lambda x: x.device):
        out.append({
            "device": p.device,
            "hwid": p.hwid or "",
            "serial": p.serial_number or "",
            "location": p.location or "",
            "desc": p.description or "",
        })
    return out


def resolve(port_match: str | list[str] | None, fallback: str | None = None) -> str:
    """
    port_match 의 모든 토큰이 hwid 에 들어 있는 포트를 찾는다.
    정확히 하나가 아니면 실패시킨다. 여러 개 걸리면
    엉뚱한 DUT 에 붙는 것보다 멈추는 게 낫다.

    토큰을 여러 개 쓰는 이유:
      LOCATION=1-3:x.0 을 통째로 쓰면 USB 허브를 다른 포트에 꽂았을 때
      1-3 이 1-4 로 바뀌어 전부 깨진다.
      SER 과 x.0 을 따로 주면 허브 위치가 바뀌어도 그대로 찾는다.

        port_match: ["SER=1B602D7E...", "x.0"]
    """
    if not port_match:
        if not fallback:
            raise PortNotFound("port_match 와 serial_port 가 모두 비어 있습니다")
        return fallback

    tokens = ([port_match] if isinstance(port_match, str)
              else [str(t) for t in port_match])
    hits = [p for p in scan() if all(t in p["hwid"] for t in tokens)]
    if len(hits) == 1:
        return hits[0]["device"]
    if not hits:
        raise PortNotFound(
            f"포트를 못 찾음: {tokens}\n"
            f"  python -m harness.ports 로 현재 목록을 확인하세요.\n"
            f"  장비 전원이나 USB 연결이 빠졌을 수도 있습니다.")
    raise PortNotFound(
        f"port_match 가 {len(hits)}개에 걸립니다: {tokens}\n"
        f"  걸린 포트: {[h['device'] for h in hits]}\n"
        f"  CP2108 같은 Quad 브리지는 Interface 0~3 이 SER 을 공유합니다.\n"
        f"  인터페이스 번호(x.0 ~ x.3)를 토큰에 추가하세요.")


def main() -> None:
    ports = scan()
    if not ports:
        print("연결된 COM 포트가 없습니다.")
        return
    print(f"{'포트':<8} {'설명':<44} hwid")
    print("-" * 110)
    for p in ports:
        print(f"{p['device']:<8} {p['desc'][:44]:<44} {p['hwid']}")
    print()
    print("config/duts.yaml 의 port_match 에는 위 hwid 에서")
    print("그 포트만 유일하게 구분되는 부분을 복사해 넣으세요.")
    print("Quad 브리지는 SER 이 겹치므로 인터페이스 번호를 함께 주세요.")
    print("LOCATION 을 통째로 넣으면 허브를 다른 USB 포트에 꽂았을 때 깨집니다.")
    print("  port_match: [\"SER=01A2B3C4\", \"x.0\"]")


if __name__ == "__main__":
    main()
