import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import live_status  # noqa: E402

FAA_SAMPLE = """<AIRPORT_STATUS_INFORMATION><Update_Time>now</Update_Time>
<Delay_type><Name>Ground Delay Programs</Name><Ground_Delay_List>
  <Ground_Delay><ARPT>EWR</ARPT><Reason>weather / low ceilings</Reason><Avg>1 hour and 5 minutes</Avg><Max>2 hours and 1 minute</Max></Ground_Delay>
</Ground_Delay_List></Delay_type>
<Delay_type><Name>Ground Stop Programs</Name><Ground_Stop_List>
  <Program><ARPT>SFO</ARPT><Reason>thunderstorms</Reason><End_Time>5:00 pm EDT</End_Time></Program>
</Ground_Stop_List></Delay_type>
<Delay_type><Name>General Arrival/Departure Delay Info</Name><Arrival_Departure_Delay_List>
  <Delay><ARPT>ORD</ARPT><Reason>VOL:Volume</Reason>
    <Arrival_Departure Type="Departure"><Min>16 minutes</Min><Max>30 minutes</Max><Trend>Increasing</Trend></Arrival_Departure>
  </Delay>
</Arrival_Departure_Delay_List></Delay_type>
<Delay_type><Name>Airport Closures</Name><Airport_Closure_List>
  <Airport><ARPT>LAX</ARPT><Reason>!LAX AD AP CLSD TO NON SKED TRANSIENT GA ACFT</Reason></Airport>
</Airport_Closure_List></Delay_type>
</AIRPORT_STATUS_INFORMATION>"""


def test_minutes():
    assert live_status.minutes("1 hour and 5 minutes") == 65
    assert live_status.minutes("31-45 minutes") == 45
    assert live_status.minutes("2 hours") == 120
    assert live_status.minutes(None) is None


def test_parse_faa_ignores_closure_notices():
    ev = live_status.parse_faa(FAA_SAMPLE)
    assert ev["EWR"][0]["kind"] == "ground_delay" and ev["EWR"][0]["avg"] == 65
    assert ev["SFO"][0]["kind"] == "ground_stop"
    assert ev["ORD"][0] == {**ev["ORD"][0], "kind": "general_delay", "side": "departure", "max": 30}
    assert "LAX" not in ev


def test_apply_only_affects_flights_in_the_next_hours(monkeypatch):
    monkeypatch.setattr(live_status, "faa_events", lambda: live_status.parse_faa(FAA_SAMPLE))
    soon = datetime.now(timezone.utc) + timedelta(hours=2)
    p, notes, typical = live_status.apply([0.8, 0.18, 0.02], "BOS", "EWR", soon)
    assert round(p[1] + p[2], 2) == 0.8 and typical == 65 and "EWR" in notes[0]
    p, notes, _ = live_status.apply([0.8, 0.18, 0.02], "ORD", "LGA", soon)   # departures delayed at ORD
    assert round(p[1] + p[2], 2) == 0.5
    p, notes, _ = live_status.apply([0.8, 0.18, 0.02], "EWR", "BOS", soon)   # EWR GDP affects arrivals only
    assert p == [0.8, 0.18, 0.02] and not notes
    later = datetime.now(timezone.utc) + timedelta(days=2)
    assert live_status.apply([0.8, 0.18, 0.02], "BOS", "EWR", later)[1] == []
