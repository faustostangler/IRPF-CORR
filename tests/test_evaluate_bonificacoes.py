import pytest
from irpf_b3.evaluate_bonificacoes import sanitize_filename, parse_year_month, get_cvm_for_ticker

def test_sanitize_filename():
    assert sanitize_filename("Fato Relevante - Bonificação de Ações") == "fato_relevante_bonificacao_de_acoes"
    assert sanitize_filename("Aumento de Capital - 2026") == "aumento_de_capital_2026"
    assert sanitize_filename("  Espaços   Múltiplos e Acentuação: áéíóúçñ ") == "espacos_multiplos_e_acentuacao_aeioucn"

def test_parse_year_month_datetime_ref():
    item = {"dateTimeReference": "2026-03-15T10:30:00"}
    year, month = parse_year_month(item)
    assert year == "2026"
    assert month == "03"

def test_parse_year_month_date_ref():
    item = {"dateReference": "25/12/2025"}
    year, month = parse_year_month(item)
    assert year == "2025"
    assert month == "12"

def test_parse_year_month_fallback():
    item = {}
    year, month = parse_year_month(item)
    # Should fallback to current year and month
    import datetime
    now = datetime.datetime.now()
    assert year == str(now.year)
    assert month == f"{now.month:02d}"

def test_get_cvm_for_ticker():
    companies = [
        {"issuingCompany": "BBAS", "tradingName": "BRASIL", "codeCVM": "1234"},
        {"issuingCompany": "ITUB", "tradingName": "ITAUNIBANCO", "codeCVM": "5678"},
    ]
    # Test prefix matching
    code, trading = get_cvm_for_ticker("BBAS3", companies)
    assert code == "1234"
    assert trading == "BRASIL"

    # Test exact issuing company match without suffix
    code, trading = get_cvm_for_ticker("ITUB4", companies)
    assert code == "5678"
    assert trading == "ITAUNIBANCO"

    # Test unmatched
    code, trading = get_cvm_for_ticker("WEGE3", companies)
    assert code is None
    assert trading is None
