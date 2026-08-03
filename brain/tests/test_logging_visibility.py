"""INFO logları Cloud Logging'e düşmeli (4 Ağu 01:19 vakası).

Vaka: onay push'unun gidip gitmediği teşhis EDİLEMEDİ, çünkü uygulamanın bütün
iz satırları (`approval_sink:`, `fcm:`, `factory:`) `logging.info` ile yazılıyor
ve root logger hiçbir yerde yapılandırılmadığı için varsayılan WARNING
seviyesinde kalıyordu — INFO üretimde stderr'e, dolayısıyla Cloud Logging'e hiç
ulaşmıyordu. (WARNING+ satırlar Python'un "last resort" handler'ıyla zaten
görünüyordu; kaybolan yalnız INFO'ydu, bu yüzden fark edilmesi gecikti.)
"""
import logging


def test_importing_main_makes_info_logging_visible():
    """ÖLDÜREN MUTASYON: main'deki logging yapılandırmasını silmek — INFO
    yeniden görünmez olur ve bir sonraki canlı vakada yine kör kalırız."""
    import app.main  # noqa: F401  (yapılandırma import yan etkisi)

    assert logging.getLogger().isEnabledFor(logging.INFO)
