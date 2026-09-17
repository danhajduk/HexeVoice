#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


def replace_once(path: Path, old: str, new: str) -> None:
    source = path.read_text(encoding="utf-8")
    if new in source:
        return
    if source.count(old) != 1:
        raise RuntimeError(f"unexpected ESP-Hosted source in {path}: patch anchor not found exactly once")
    path.write_text(source.replace(old, new), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Make ESP-Hosted 1.4.7 tolerate transient DMA OOM.")
    parser.add_argument("component", type=Path)
    args = parser.parse_args()

    transport = args.component / "host/drivers/transport/transport_drv.c"
    sdio = args.component / "host/drivers/transport/sdio/sdio_drv.c"
    if not transport.is_file() or not sdio.is_file():
        raise RuntimeError(f"ESP-Hosted source files are missing below {args.component}")

    tx_failure = """\
\tif (!copy_buff) {
\t\terrno = -ENOBUFS;
#if defined(ESP_ERR_ESP_NETIF_TX_FAILED)
\t\treturn ESP_ERR_ESP_NETIF_TX_FAILED;
#else
\t\treturn ESP_ERR_ESP_NETIF_NO_MEM;
#endif
\t}"""
    replace_once(
        transport,
        "\tcopy_buff = mempool_alloc(((struct mempool*)chan_arr[ESP_STA_IF]->memp), MAX_TRANSPORT_BUFFER_SIZE, true);\n\tassert(copy_buff);",
        "\tcopy_buff = mempool_alloc(((struct mempool*)chan_arr[ESP_STA_IF]->memp), MAX_TRANSPORT_BUFFER_SIZE, true);\n" + tx_failure,
    )
    replace_once(
        transport,
        "\tcopy_buff = mempool_alloc(((struct mempool*)chan_arr[ESP_AP_IF]->memp), MAX_TRANSPORT_BUFFER_SIZE, true);\n\tassert(copy_buff);",
        "\tcopy_buff = mempool_alloc(((struct mempool*)chan_arr[ESP_AP_IF]->memp), MAX_TRANSPORT_BUFFER_SIZE, true);\n" + tx_failure,
    )
    replace_once(
        sdio,
        "\t\t\tsendbuf = sdio_buffer_alloc(MEMSET_REQUIRED);\n\t\t\tassert(sendbuf);",
        "\t\t\tsendbuf = sdio_buffer_alloc(MEMSET_REQUIRED);",
    )
    replace_once(
        sdio,
        "\t\tpkt_rxbuff = sdio_buffer_alloc(MEMSET_REQUIRED);\n\t\tassert(pkt_rxbuff);",
        "\t\tpkt_rxbuff = sdio_buffer_alloc(MEMSET_REQUIRED);\n\t\tif (!pkt_rxbuff) {\n\t\t\tESP_LOGE(TAG, \"Dropping SDIO RX stream: no DMA buffer\");\n\t\t\treturn ESP_ERR_NO_MEM;\n\t\t}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
