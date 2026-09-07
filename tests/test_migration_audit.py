import json
import pandas as pd
import pytest
from us_equity_alpha.mapping import map_field, map_library
from us_equity_alpha.factors import evaluate_factor, UnsupportedExpression


def test_concept_lookup_is_not_a_direct_provider_field():
    row = map_field({'id':'assets','dataset':'fundamental6','type':'MATRIX'})
    assert row['mapping_class'] == 'UNKNOWN'
    assert row['candidates']['tiingo'] is None


def test_daily_shares_and_option_surfaces_are_not_generic_bars():
    shares=map_field({'id':'sharesout','dataset':'pv1','type':'MATRIX'})
    assert shares['candidates']['alpaca'] is None
    assert shares['mapping_class']=='UNKNOWN'
    options=map_field({'id':'implied_volatility_mean_30','dataset':'option8','type':'MATRIX'})
    assert options['candidates']['alpaca'] is None
    assert options['mapping_class']=='SPECIALIST_REQUIRED'


def test_full_brain_settings_cannot_be_silently_ignored():
    with pytest.raises(UnsupportedExpression, match='UNIMPLEMENTED_SETTINGS'):
        evaluate_factor('rank(close)', {'close':pd.DataFrame({'A':[1.,2.]})},
                        {'delay':1,'decay':0,'neutralization':'NONE','truncation':0.02,'pasteurization':'ON','universe':'TOP3000'})


def test_noninteger_delay_is_rejected():
    with pytest.raises(UnsupportedExpression):
        evaluate_factor('rank(close)',{'close':pd.DataFrame({'A':[1.,2.]})},
                        {'delay':1.5,'decay':0,'neutralization':'NONE'})


def test_settings_dependencies_appear_in_field_catalog(tmp_path):
    p=tmp_path/'registry.json'; c=tmp_path/'catalog.json'
    p.write_text(json.dumps({'records':[{'alpha_id':'example','settings':{'neutralization':'SECTOR'},'dependencies':{'safe':True,'fields':[{'identifier':'close'}]}}]}))
    c.write_text(json.dumps([{'id':'close','dataset':'pv1','type':'MATRIX'}]))
    map_library(p,c,tmp_path/'out')
    fields=json.loads((tmp_path/'out/field_mapping.json').read_text())
    assert 'sector' in {x['field_id'] for x in fields}
