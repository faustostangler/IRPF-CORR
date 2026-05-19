import json
import urllib.request
from datetime import date, timedelta
from decimal import Decimal


def get_ptax(target_date: date) -> Decimal:
    """
    Fetches the PTAX exchange rate (venda) for the given date from the Central Bank of Brazil.
    If the date is a weekend or holiday, it looks backward for up to 5 days to find the last available rate.
    """
    for i in range(5):
        current_date = target_date - timedelta(days=i)
        date_str = current_date.strftime("%m-%d-%Y")
        url = f"https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/CotacaoDolarDia(dataCotacao=@dataCotacao)?@dataCotacao='{date_str}'&$top=1&$format=json"

        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode())
                if data and data.get("value"):
                    return Decimal(str(data["value"][0]["cotacaoVenda"]))
        except Exception as e:
            print(f"Error fetching PTAX for {current_date}: {e}")
            break

    print(f"Warning: Could not fetch PTAX for {target_date}.")
    return Decimal("1")
