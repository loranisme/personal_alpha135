import pytest
from us_equity_alpha.paper_log import PaperLog

def signal(sid="s1"):
    return {"signal_id":sid,"signal_session":"2025-01-02","pool_hash":"a"*64,"universe_hash":"b"*64,"config_hash":"c"*64,"input_hashes":{"bars":"d"*64},"blockers":[],"target_hash":"e"*64,"execution_helper_status":"NOT_REQUESTED","helper_output_hash":None,"live_orders_submitted":0}
def settlement(sid="s1"):
    return {"signal_id":sid,"entry_reference":100,"exit_reference":105,"five_session_total_return":.05,"missing_reason":None,"settled_at":"2025-01-10T22:00:00Z"}

def test_signal_and_settlement_are_separate_append_only_events(tmp_path):
    log=PaperLog(tmp_path/"paper.sqlite");log.record_signal(signal());log.settle_signal(settlement())
    rows=log.export()["events"]
    assert [x["kind"] for x in rows]==["SIGNAL","SETTLEMENT"]
    assert rows[0]["payload"]==signal()

def test_duplicate_signal_and_unknown_settlement_are_rejected(tmp_path):
    log=PaperLog(tmp_path/"paper.sqlite");log.record_signal(signal())
    with pytest.raises(ValueError,match="DUPLICATE_SIGNAL_ID"):log.record_signal(signal())
    with pytest.raises(ValueError,match="UNKNOWN_SIGNAL_ID"):log.settle_signal(settlement("missing"))
