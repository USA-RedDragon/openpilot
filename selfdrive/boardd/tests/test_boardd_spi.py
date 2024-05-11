#!/usr/bin/env python3
import os
import time
import numpy as np
import pytest

import cereal.messaging as messaging
from cereal.services import SERVICE_LIST
from openpilot.system.hardware import HARDWARE
from openpilot.selfdrive.test.helpers import phone_only, with_processes
from openpilot.selfdrive.boardd.tests.test_boardd_loopback import setup_boardd, send_random_can_messages


@pytest.mark.tici
class TestBoarddSpi:
  @classmethod
  def setup_class(cls):
    if HARDWARE.get_device_type() == 'tici':
      pytest.skip("only for spi pandas")
    os.environ['STARTED'] = '1'
    os.environ['BOARDD_LOOPBACK'] = '1'
    os.environ['SPI_ERR_PROB'] = '0.001'

  @phone_only
  @with_processes(['pandad'])
  def test_spi_corruption(self, subtests):
    setup_boardd(1)

    sendcan = messaging.pub_sock('sendcan')
    socks = {s: messaging.sub_sock(s, conflate=False, timeout=100) for s in ('can', 'pandaStates', 'peripheralState')}
    time.sleep(2)
    for s in socks.values():
      messaging.drain_sock_raw(s)



    total_recv_count = 0
    total_sent_count = 0
    sent_msgs = {bus: list() for bus in range(3)}

    st = time.monotonic()
    ts = {s: list() for s in socks.keys()}
    for _ in range(20):
      for service, sock in socks.items():
        for m in messaging.drain_sock(sock):
          ts[service].append(m.logMonoTime)

          # sanity check for corruption
          assert m.valid
          if service == "can":
            for msg in m.can:
              if msg.src > 4:
                continue
              key = (msg.address, msg.dat)
              assert key in sent_msgs[msg.src], f"got unexpected msg: {msg.src=} {msg.address=} {msg.dat=}"
              sent_msgs[msg.src].remove(key)
              total_recv_count += 1
          elif service == "pandaStates":
            assert len(m.pandaStates) == 1
            ps = m.pandaStates[0]
            assert ps.uptime < 100
            assert ps.pandaType == "tres"
            assert ps.ignitionLine
            assert not ps.ignitionCan
            assert ps.voltage < 14000
          elif service == "peripheralState":
            ps = m.peripheralState
            assert ps.pandaType == "tres"
            assert 4000 < ps.voltage < 14000
            assert 100 < ps.current < 1000
            assert ps.fanSpeedRpm < 8000

      time.sleep(0.5)
    et = time.monotonic() - st

    print("\n======== timing report ========")
    for service, times in ts.items():
      dts = np.diff(times)/1e6
      print(service.ljust(17), f"{np.mean(dts):7.2f} {np.min(dts):7.2f} {np.max(dts):7.2f}")
      with subtests.test(msg="timing check", service=service):
        edt = 1e3 / SERVICE_LIST[service].frequency
        assert edt*0.9 < np.mean(dts) < edt*1.1
        assert np.max(dts) < edt*3
        assert np.min(dts) < edt
        assert len(dts) >= ((et-0.5)*SERVICE_LIST[service].frequency*0.8)

    with subtests.test(msg="CAN traffic"):
      print(f"Sent {total_sent_count} CAN messages, got {total_recv_count} back. {total_recv_count/total_sent_count:.2%} received")
      assert (total_recv_count / total_sent_count) > 0.8