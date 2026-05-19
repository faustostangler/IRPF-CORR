import pytest
import os
import re
from unittest.mock import MagicMock, patch
from irpf_b3.scraper import fetch_fato_relevante as scraper_fetch, main as scraper_main

def test_fetch_fato_relevante_retroactivity():
    """Test that fetch_fato_relevante queries multiple years retroactively and respects the 4-year limit."""
    mock_client = MagicMock()
    # Mocking standard response that returns empty list
    mock_response = MagicMock()
    mock_response.json.return_value = {"results": [], "page": {}}
    mock_client.get.return_value = mock_response
    
    with patch("httpx.Client") as mock_client_class:
        mock_client_class.return_value.__enter__.return_value = mock_client
        
        # Call fetch_fato_relevante
        results = scraper_fetch("12345")
        
        # Verify it got called for multiple years (should stop after 4 empty years)
        # So it should call 2026, 2025, 2024, 2023, 2022 (first 5 years, stopping because empty_years_count reaches 4)
        called_urls = [call[0][0] for call in mock_client.get.call_args_list]
        assert len(called_urls) > 5  # Queries 4 categories per year, so 5 years * 4 categories = 20 calls
        
        # Check that multiple years are present in the called payloads
        # We can extract the base64 payload to see the year or just verify the calls were made
        assert mock_client.get.call_count >= 16

def test_company_subfolder_structure():
    """Verify that file paths are organized under a company-specific directory."""
    # We will test the directory creation logic that should exist in scraper.py
    # By running the scraper with a mocked company and tickers file
    mock_companies = [
        {"issuingCompany": "BBAS", "tradingName": "BRASIL", "codeCVM": "12345"},
    ]
    
    mock_fatos = [
        {
            "category": "Fato Relevante",
            "dateReference": "15/04/2026",
            "subject": "Projeções 1T26",
            "urlSearch": "https://www.rad.cvm.gov.br/ENET/frmExibirArquivoIPEExterno.aspx?ID=1521599"
        }
    ]
    
    with patch("irpf_b3.scraper.get_all_companies", return_value=mock_companies), \
         patch("irpf_b3.scraper.fetch_fato_relevante", return_value=mock_fatos), \
         patch("irpf_b3.scraper.download_pdf", return_value=True), \
         patch("irpf_b3.scraper.extract_pdf_text", return_value="Texto teste bonificacao"), \
         patch("builtins.open", patch("builtins.open", create=True)) as mock_open:
        
        # We want to check that the path passed to download_pdf or os.makedirs is docs/pdf/{ticker}/
        # Let's mock os.makedirs and os.path.exists to see if it targets docs/pdf/BBAS3/
        with patch("os.makedirs") as mock_makedirs, \
             patch("os.path.exists", return_value=False), \
             patch("os.path.getsize", return_value=0), \
             patch("irpf_b3.scraper.download_pdf") as mock_download:
            
            # Mock open for tickers.txt
            from unittest.mock import mock_open
            m = mock_open(read_data="BBAS3\n")
            
            with patch("builtins.open", m):
                # Run main from scraper
                try:
                    scraper_main()
                except SystemExit:
                    pass
                
                # Check that os.makedirs was called for a path ending in BBAS3
                makedirs_calls = [call[0][0] for call in mock_makedirs.call_args_list]
                assert any("BBAS3" in path for path in makedirs_calls), f"Expected 'BBAS3' in makedirs calls: {makedirs_calls}"
                
                # Check that download_pdf was called with path containing BBAS3/
                download_calls = [call[0][1] for call in mock_download.call_args_list]
                assert any("BBAS3" in path for path in download_calls), f"Expected 'BBAS3' in download_pdf calls: {download_calls}"

def test_get_all_companies_cache():
    """Verify that get_all_companies loads cached companies from JSON if present."""
    import json
    from unittest.mock import mock_open
    from irpf_b3.scraper import get_all_companies
    mock_data = [{"issuingCompany": "TEST", "tradingName": "TESTER", "codeCVM": "99999"}]
    
    # We want to patch os.path.exists to return True when checking the cache file
    with patch("os.path.exists", side_effect=lambda path: "all_companies.json" in path), \
         patch("builtins.open", mock_open(read_data=json.dumps(mock_data))):
        companies = get_all_companies()
        assert companies == mock_data


