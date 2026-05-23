from flask import Flask, request, jsonify
import requests
from bs4 import BeautifulSoup
import re
import json
import base64
import time
import os

app = Flask(__name__)
app.json.ensure_ascii = False  # Türkçe karakterler düzgün görünsün

# ==================== TÜRKÇE DÜZELTME (İlk koddan) ====================
def unicode_duzelt(text):
    if not text or not isinstance(text, str):
        return text
    
    try:
        text = text.encode('latin-1').decode('utf-8')
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
        
    try:
        text = text.encode().decode('unicode-escape')
    except:
        pass
        
    return text

# ==================== 1. SICIL SORGULA (ITO) ====================
class SicilAPI:
    def __init__(self):
        self.base_url = "https://eportal.ito.org.tr/v2/api"
        self.session = requests.Session()
        self.session.get("https://eportal.ito.org.tr/ihale-tarihli-faaliyet-belgesi")

    def sorgula(self, sicil_no: int):
        url = f"{self.base_url}/Company/list"
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=UTF-8",
            "Origin": "https://eportal.ito.org.tr",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        response = self.session.post(url, headers=headers, json={"sicno": sicil_no})
        data = response.json()
        if data.get('data', {}).get('data'):
            for item in data['data']['data']:
                if 'title' in item:
                    item['title'] = unicode_duzelt(item['title'])
                if 'address' in item:
                    item['address'] = unicode_duzelt(item['address'])
                if 'neviGroupName' in item:
                    item['neviGroupName'] = unicode_duzelt(item['neviGroupName'])
        return data

# ==================== 2. PLAKA CEZA ====================
class PlakaCezaAPI:
    def __init__(self):
        self.base_url = "https://otoyakalama.com"
        self.session = requests.Session()
        self.csrf_token = None

    def _refresh_token(self):
        resp = self.session.get(f"{self.base_url}/")
        soup = BeautifulSoup(resp.text, 'html.parser')
        token = soup.find('meta', {'name': 'csrf-token'})
        if token:
            self.csrf_token = token.get('content', '')
        return self.csrf_token

    def sorgula(self, plaka: str):
        if not self.csrf_token:
            self._refresh_token()
        url = f"{self.base_url}/plate-query"
        headers = {
            "X-CSRF-TOKEN": self.csrf_token,
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        data = {"plate_number": plaka.upper(), "latitude": 0, "longitude": 0}
        response = self.session.post(url, headers=headers, data=data)
        result = response.json()
        if result.get('data') and len(result['data']) > 0:
            d = result['data'][0]
            return {
                "plaka": plaka.upper(),
                "durum": "Ceza var" if d.get('Durum') == '1' else "Ceza yok",
                "marka": unicode_duzelt(d.get('Marka', '')),
                "model": unicode_duzelt(d.get('Model', '')),
                "renk": unicode_duzelt(d.get('Renk', ''))
            }
        return {"plaka": plaka.upper(), "durum": "Sonuc bulunamadi"}

# ==================== 3. HAK SAHİPLİĞİ (Danıştay) ====================
class HakSahipligiAPI:
    def __init__(self):
        self.url = "https://api.danistay.gov.tr/api/v1/tr/saglik/sorgulama"
        self.session = requests.Session()

    def sorgula(self, tc_no: str):
        headers = {
            "accept": "application/json, text/plain, */*",
            "content-type": "application/json;charset=UTF-8",
            "origin": "https://www.danistay.gov.tr",
            "referer": "https://www.danistay.gov.tr/",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        response = self.session.post(self.url, headers=headers, json={"tc": tc_no})
        result = response.json()
        if result and len(result) > 0:
            return {
                "tc_no": tc_no,
                "hak_sahibi": result[0].get("sonuc", False),
                "dogrulama": result[0].get("dogrulama", False),
                "mesaj": "Hak sahibi" if result[0].get("sonuc") else "Hak sahibi degil"
            }
        return {"tc_no": tc_no, "hak_sahibi": False, "dogrulama": False, "mesaj": "Sonuc alinamadi"}

# ==================== 4. SINAV SONUCU ====================
class SinavAPI:
    def __init__(self):
        self.base_url = "https://sonuc.sinav.pro"
        self.session = requests.Session()
    
    def turleri_getir(self):
        resp = self.session.get(self.base_url)
        soup = BeautifulSoup(resp.text, 'html.parser')
        select = soup.find('select', {'id': re.compile('_sinavliste')})
        options = []
        if select:
            for opt in select.find_all('option'):
                if opt.get('value'):
                    options.append({
                        "adi": unicode_duzelt(opt.text.strip()),
                        "degeri": opt.get('value')
                    })
        return options
    
    def sonuc_sorgula(self, tc_no: str, sinav_turu: str = "tyt"):
        resp = self.session.get(self.base_url)
        soup = BeautifulSoup(resp.text, 'html.parser')
        viewstate = soup.find('input', {'name': '__VIEWSTATE'})
        eventvalidation = soup.find('input', {'name': '__EVENTVALIDATION'})
        data = {
            "__VIEWSTATE": viewstate.get('value', '') if viewstate else '',
            "__EVENTVALIDATION": eventvalidation.get('value', '') if eventvalidation else '',
            "ctl00$ContentPlaceHolder1$_sinavliste": sinav_turu,
            "ctl00$ContentPlaceHolder1$_tcno": tc_no,
            "ctl00$ContentPlaceHolder1$_arama": "Sonuç Ara"
        }
        response = self.session.post(self.base_url, data=data)
        soup_result = BeautifulSoup(response.text, 'html.parser')
        table = soup_result.find('table', {'class': re.compile('table', re.I)})
        if not table:
            return {"durum": "sonuc_yok", "mesaj": "Kayit bulunamadi"}
        sonuclar = []
        for row in table.find_all('tr'):
            cols = row.find_all('td')
            if cols:
                temiz = [unicode_duzelt(col.get_text(strip=True)) for col in cols]
                sonuclar.append(temiz)
        return {"durum": "basarili", "sonuclar": sonuclar, "toplam": len(sonuclar)}

# ==================== 5. GİG HAK SAHİPLİĞİ ====================
class GIGAPI:
    def __init__(self):
        self.url = "https://client.gig.com.tr/webservice/right-holders/name"
        self.session = requests.Session()
    
    def sorgula(self, ad: str, soyad: str, dogum_tarihi: str):
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=UTF-8",
            "Origin": "https://www.gig.com.tr",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        data = {
            "name": ad.upper(),
            "surname": soyad.upper(),
            "date": dogum_tarihi
        }
        response = self.session.post(self.url, headers=headers, json=data)
        return response.json()

# ==================== 6. İSTANBUL BAROSU AVUKAT ====================
class IstanbulBaroAPI:
    def __init__(self):
        self.base_url = "https://www.istanbulbarosu.org.tr"
        self.session = requests.Session()
    
    def avukat_sorgula(self, sicil: str = None, ad: str = None, soyad: str = None):
        url = f"{self.base_url}/levha"
        params = {"unvan": "1"}
        if sicil:
            params["sicil"] = sicil
        if ad:
            params["firstName"] = ad
        if soyad:
            params["lastName"] = soyad
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        response = self.session.get(url, params=params, headers=headers)
        soup = BeautifulSoup(response.text, 'html.parser')
        tablo = soup.find('table', {'class': re.compile('table', re.I)})
        if not tablo:
            return {"durum": "bulunamadi", "mesaj": "Avukat bulunamadi"}
        avukatlar = []
        for row in tablo.find_all('tr')[1:]:
            cols = row.find_all('td')
            if len(cols) >= 3:
                avukatlar.append({
                    "sicil": unicode_duzelt(cols[0].get_text(strip=True)),
                    "ad_soyad": unicode_duzelt(cols[1].get_text(strip=True)),
                    "durum": unicode_duzelt(cols[2].get_text(strip=True))
                })
        return {"durum": "basarili", "avukat_sayisi": len(avukatlar), "avukatlar": avukatlar}

# ==================== 7. ANKARA BAROSU AVUKAT ====================
class AnkaraBaroAPI:
    def __init__(self):
        self.url = "https://www.ankarabarosu.org.tr/data-source/"
        self.base_url = "https://www.ankarabarosu.org.tr"
        self.session = requests.Session()
    
    def _foto_indir_base64(self, foto_url):
        if not foto_url or "no-image" in foto_url:
            return None
        try:
            full_url = self.base_url + foto_url if foto_url.startswith('/') else foto_url
            response = self.session.get(full_url, timeout=10)
            if response.status_code == 200:
                return base64.b64encode(response.content).decode('utf-8')
        except:
            pass
        return None
    
    def avukat_sorgula(self, ad: str = None, soyad: str = None, sicil: str = None):
        searched = {}
        if ad:
            searched["name"] = ad
        if soyad:
            searched["surname"] = soyad
        if sicil:
            searched["sicil"] = sicil
        params = {
            "draw": 1,
            "start": 0,
            "length": -1,
            "search[value]": "",
            "search[regex]": "false",
            "searched": json.dumps(searched)
        }
        headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": "https://www.ankarabarosu.org.tr/avukatlar/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "X-Requested-With": "XMLHttpRequest"
        }
        response = self.session.get(self.url, params=params, headers=headers)
        result = response.json()
        avukat_listesi = []
        for item in result.get('data', []):
            if len(item) >= 5:
                foto_url = None
                img_soup = BeautifulSoup(item[1], 'html.parser')
                img = img_soup.find('img')
                if img and img.get('src'):
                    foto_url = img.get('src')
                sira_soup = BeautifulSoup(item[0], 'html.parser')
                sicil_soup = BeautifulSoup(item[2], 'html.parser')
                ad_soup = BeautifulSoup(item[3], 'html.parser')
                soyad_soup = BeautifulSoup(item[4], 'html.parser')
                avukat = {
                    "sira": unicode_duzelt(sira_soup.get_text(strip=True)),
                    "sicil": unicode_duzelt(sicil_soup.get_text(strip=True)),
                    "ad": unicode_duzelt(ad_soup.get_text(strip=True)),
                    "soyad": unicode_duzelt(soyad_soup.get_text(strip=True)),
                    "foto_base64": self._foto_indir_base64(foto_url)
                }
                avukat_listesi.append(avukat)
        return {
            "durum": "basarili",
            "toplam": result.get('recordsTotal', 0),
            "avukatlar": avukat_listesi
        }

# ==================== 8. THY UÇUŞ DURUMU ====================
class ThyUcusAPI:
    def __init__(self):
        self.url = "https://www.turkishairlines.com/com.thy.web.online.deparr/deparr/departurearrivals/byflight"
        self.session = requests.Session()
    
    def ucus_durumu_sorgula(self, ucus_no: str, tarih: str):
        headers = {
            "accept": "*/*",
            "accept-language": "tr",
            "content-type": "application/json; charset=UTF-8",
            "origin": "https://www.turkishairlines.com",
            "referer": "https://www.turkishairlines.com/tr-tr/ucak-bileti/ucus-durumu/",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "x-requested-with": "XMLHttpRequest"
        }
        data = {"flightNumber": ucus_no, "departureDate": tarih}
        response = self.session.post(self.url, headers=headers, json=data)
        result = response.json()
        if result.get("type") == "SUCCESS" and result.get("data", {}).get("flights"):
            flight = result["data"]["flights"][0]
            segment = flight.get("segments", [{}])[0]
            return {
                "durum": "basarili",
                "ucus_no": ucus_no,
                "tarih": tarih,
                "kalkis": {
                    "havalimani": unicode_duzelt(segment.get("originAirport", {}).get("name", "")),
                    "kodu": segment.get("originAirport", {}).get("code", ""),
                    "saat": segment.get("departureDateTimeISO", {}).get("hourMinuteLocal", ""),
                    "terminal": unicode_duzelt(segment.get("departureTerminalInfo", ""))
                },
                "varis": {
                    "havalimani": unicode_duzelt(segment.get("destinationAirport", {}).get("name", "")),
                    "kodu": segment.get("destinationAirport", {}).get("code", ""),
                    "saat": segment.get("arrivalDateTimeISO", {}).get("hourMinuteLocal", ""),
                    "terminal": segment.get("arrivalTerminalInfo", "")
                },
                "ucak_tipi": segment.get("aircraftType", ""),
                "seyahat_suresi": "4 saat",
                "havayolu": "Turkish Airlines"
            }
        return {"durum": "hata", "ucus_no": ucus_no, "mesaj": "Ucus bilgisi bulunamadi"}

# ==================== 9. TFF HAKEM ARAMA ====================
class TFFHakemAPI:
    def __init__(self):
        self.base_url = "https://www.tff.org"
        self.session = requests.Session()
    
    def hakem_ara(self, ad: str = None, soyad: str = None, sehir: str = None, klasman: str = None):
        url = f"{self.base_url}/default.aspx?pageID=161"
        resp = self.session.get(url)
        soup = BeautifulSoup(resp.text, 'html.parser')
        viewstate = soup.find('input', {'name': '__VIEWSTATE'})
        eventvalidation = soup.find('input', {'name': '__EVENTVALIDATION'})
        data = {
            '__VIEWSTATE': viewstate.get('value', '') if viewstate else '',
            '__EVENTVALIDATION': eventvalidation.get('value', '') if eventvalidation else '',
            '__VIEWSTATEGENERATOR': 'CA0B0334',
            'ctl00$MPane$m_161_851_ctnr$m_161_851$txtAd': ad or '',
            'ctl00$MPane$m_161_851_ctnr$m_161_851$txtSoyad': soyad or '',
            'ctl00$MPane$m_161_851_ctnr$m_161_851$f_value': '1',
            'ctl00$MPane$m_161_851_ctnr$m_161_851$f_text': 'Faal',
            'ctl00$MPane$m_161_851_ctnr$m_161_851$btnSave': 'Ara',
            'ctl00$MPane$m_161_851_ctnr$m_161_851$cmbKlasman_value': klasman or '',
            'ctl00$MPane$m_161_851_ctnr$m_161_851$SehirSelector1$combo_value': sehir or ''
        }
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Content-Type': 'application/x-www-form-urlencoded',
            'Referer': url
        }
        response = self.session.post(url, headers=headers, data=data)
        soup_result = BeautifulSoup(response.text, 'html.parser')
        tablo = soup_result.find('table', {'id': re.compile('rdgSonuclar', re.I)})
        if tablo:
            hakemler = []
            for row in tablo.find_all('tr')[1:]:
                cols = row.find_all('td')
                if len(cols) >= 5:
                    hakemler.append({
                        "ad_soyad": unicode_duzelt(cols[0].get_text(strip=True)),
                        "il": unicode_duzelt(cols[1].get_text(strip=True)),
                        "klasman": unicode_duzelt(cols[2].get_text(strip=True)),
                        "durum": unicode_duzelt(cols[3].get_text(strip=True)),
                        "lisans_no": cols[4].get_text(strip=True)
                    })
            return {"durum": "basarili", "hakem_sayisi": len(hakemler), "hakemler": hakemler}
        return {"durum": "bulunamadi", "mesaj": "Hakem bulunamadi"}

# ==================== 10. MEPA ENERJİ FATURA ====================
class MepaEnerjiAPI:
    def __init__(self):
        self.base_url = "https://oim.mepasenerji.com"
        self.session = requests.Session()
    
    def borc_sorgula(self, tesisat_no: str):
        url = f"{self.base_url}/Anasayfa/BorcSorgula"
        resp = self.session.get(url)
        soup = BeautifulSoup(resp.text, 'html.parser')
        viewstate = soup.find('input', {'name': '__VIEWSTATE'})
        eventvalidation = soup.find('input', {'name': '__EVENTVALIDATION'})
        data = {
            '__VIEWSTATE': viewstate.get('value', '') if viewstate else '',
            '__EVENTVALIDATION': eventvalidation.get('value', '') if eventvalidation else '',
            'txtTesisatNo': tesisat_no,
            'btnSorgula': 'Sorgula'
        }
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        response = self.session.post(url, headers=headers, data=data)
        soup_result = BeautifulSoup(response.text, 'html.parser')
        borc_div = soup_result.find('div', {'class': re.compile('borc|fatura|odeme', re.I)})
        if borc_div:
            return {
                "durum": "basarili",
                "tesisat_no": tesisat_no,
                "borc": unicode_duzelt(borc_div.get_text(strip=True))
            }
        return {"durum": "hata", "tesisat_no": tesisat_no, "mesaj": "Fatura bulunamadi"}

# ==================== 11. ARAÇ BİLGİSİ (otobuluruz.com) ====================
class AracBilgiAPI:
    def __init__(self):
        self.url = "http://otobuluruz.com/data/aranan-cache.json"
        self.session = requests.Session()
    
    def arac_bilgisi_getir(self, plaka: str = None):
        headers = {
            "Accept": "*/*",
            "Accept-Language": "tr-TR,tr;q=0.9",
            "Referer": "http://otobuluruz.com/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        params = {"v": int(time.time() * 1000)}
        try:
            response = self.session.get(self.url, headers=headers, params=params, timeout=10)
            data = response.json()
        except:
            return {"durum": "hata", "mesaj": "Veri alinamadi"}
        if not isinstance(data, dict):
            return {"durum": "hata", "mesaj": "Veri formatı hatalı"}
        if plaka:
            plaka_ust = plaka.upper()
            for key, value in data.items():
                if isinstance(value, list) and len(value) > 0 and isinstance(value[0], list) and len(value[0]) > 0:
                    if value[0][0].upper() == plaka_ust:
                        return {
                            "durum": "basarili",
                            "plaka": value[0][0],
                            "marka": unicode_duzelt(value[0][1]) if len(value[0]) > 1 else "",
                            "model": unicode_duzelt(value[0][2]) if len(value[0]) > 2 else "",
                            "yil": value[0][3] if len(value[0]) > 3 else ""
                        }
            return {"durum": "bulunamadi", "plaka": plaka, "mesaj": "Plaka bulunamadi"}
        araclar = []
        for key, value in data.items():
            if isinstance(value, list) and len(value) > 0 and isinstance(value[0], list) and len(value[0]) > 0:
                araclar.append({
                    "plaka": value[0][0],
                    "marka": unicode_duzelt(value[0][1]) if len(value[0]) > 1 else "",
                    "model": unicode_duzelt(value[0][2]) if len(value[0]) > 2 else "",
                    "yil": value[0][3] if len(value[0]) > 3 else ""
                })
        return {"durum": "basarili", "toplam": len(araclar), "araclar": araclar[:20]}

# API nesneleri
sicil_api = SicilAPI()
plaka_api = PlakaCezaAPI()
hak_api = HakSahipligiAPI()
sinav_api = SinavAPI()
gig_api = GIGAPI()
istanbul_baro = IstanbulBaroAPI()
ankara_baro = AnkaraBaroAPI()
thy_ucus = ThyUcusAPI()
tff_hakem = TFFHakemAPI()
mepa = MepaEnerjiAPI()
arac_bilgi = AracBilgiAPI()

# ==================== ENDPOINTLER ====================

@app.route('/api/sicil-sorgula/<int:sicil_no>', methods=['GET'])
def sicil_sorgula(sicil_no):
    try:
        return jsonify(sicil_api.sorgula(sicil_no))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/plaka-ceza-sorgula', methods=['GET'])
def plakaceza_sorgula():
    plaka = request.args.get('plaka', '').upper()
    if not plaka:
        return jsonify({"error": "Plaka gerekli"}), 400
    try:
        return jsonify(plaka_api.sorgula(plaka))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/hak-sahipligi-sorgula/<tc>', methods=['GET'])
def haksahipligi_sorgula(tc):
    if len(tc) != 11 or not tc.isdigit():
        return jsonify({"error": "11 haneli TC gerekli"}), 400
    try:
        return jsonify(hak_api.sorgula(tc))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/sinav-turleri', methods=['GET'])
def sinav_turleri():
    try:
        return jsonify(sinav_api.turleri_getir())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/sinav-sonuc-sorgula', methods=['GET'])
def sinav_sonuc():
    tc = request.args.get('tc', '')
    tur = request.args.get('tur', 'tyt')
    if not tc or len(tc) != 11:
        return jsonify({"error": "11 haneli TC gerekli"}), 400
    try:
        return jsonify(sinav_api.sonuc_sorgula(tc, tur))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/gig-hak-sahipligi-sorgula', methods=['GET'])
def gig_sorgula():
    ad = request.args.get('ad', '').strip()
    soyad = request.args.get('soyad', '').strip()
    dt = request.args.get('dt', '').strip()
    if not ad or not soyad or not dt:
        return jsonify({"error": "ad, soyad ve dt (gun/ay/yil) gerekli"}), 400
    try:
        return jsonify(gig_api.sorgula(ad, soyad, dt))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/avukat-sorgula-istanbul', methods=['GET'])
def istanbul_baro_sorgula():
    sicil = request.args.get('sicil', '')
    ad = request.args.get('ad', '')
    soyad = request.args.get('soyad', '')
    if not sicil and not ad and not soyad:
        return jsonify({"error": "sicil veya ad/soyad gerekli"}), 400
    try:
        return jsonify(istanbul_baro.avukat_sorgula(sicil=sicil, ad=ad, soyad=soyad))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/avukat-sorgula-ankara', methods=['GET'])
def ankara_baro_sorgula():
    ad = request.args.get('ad', '')
    soyad = request.args.get('soyad', '')
    sicil = request.args.get('sicil', '')
    if not ad and not soyad and not sicil:
        return jsonify({"error": "ad/soyad veya sicil gerekli"}), 400
    try:
        return jsonify(ankara_baro.avukat_sorgula(ad=ad, soyad=soyad, sicil=sicil))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/ucus-durumu', methods=['GET'])
def ucus_durumu():
    ucus_no = request.args.get('no', '')
    tarih = request.args.get('tarih', '')
    if not ucus_no or not tarih:
        return jsonify({"error": "ucus_no ve tarih gerekli (YYYY-MM-DD)"}), 400
    try:
        return jsonify(thy_ucus.ucus_durumu_sorgula(ucus_no, tarih))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/hakem-ara', methods=['GET'])
def hakem_ara():
    ad = request.args.get('ad', '')
    soyad = request.args.get('soyad', '')
    sehir = request.args.get('sehir', '')
    klasman = request.args.get('klasman', '')
    if not ad and not soyad:
        return jsonify({"error": "ad veya soyad gerekli"}), 400
    try:
        return jsonify(tff_hakem.hakem_ara(ad=ad, soyad=soyad, sehir=sehir, klasman=klasman))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/enerji-fatura-borc-sorgula', methods=['GET'])
def enerji_borc_sorgula():
    tesisat = request.args.get('tesisat', '')
    if not tesisat:
        return jsonify({"error": "tesisat no gerekli"}), 400
    try:
        return jsonify(mepa.borc_sorgula(tesisat))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/arac-bilgisi', methods=['GET'])
def arac_bilgisi_sorgula():
    plaka = request.args.get('plaka', '').upper()
    try:
        return jsonify(arac_bilgi.arac_bilgisi_getir(plaka if plaka else None))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/', methods=['GET'])
def index():
    return jsonify({
        "api": "Sorgulama API",
        "endpointler": {
            "sicil_sorgula": "/api/sicil-sorgula/340",
            "plaka_ceza_sorgula": "/api/plaka-ceza-sorgula?plaka=34APP328",
            "hak_sahipligi_sorgula": "/api/hak-sahipligi-sorgula/11111111110",
            "sinav_turleri": "/api/sinav-turleri",
            "sinav_sonuc_sorgula": "/api/sinav-sonuc-sorgula?tc=11111111110&tur=tyt",
            "gig_hak_sahipligi_sorgula": "/api/gig-hak-sahipligi-sorgula?ad=ROKET&soyad=ATAR&dt=16/03/1998",
            "avukat_sorgula_istanbul": "/api/avukat-sorgula-istanbul?sicil=340",
            "avukat_sorgula_ankara": "/api/avukat-sorgula-ankara?ad=Mehmet&soyad=Yilmaz",
            "ucus_durumu": "/api/ucus-durumu?no=1987&tarih=2026-05-22",
            "hakem_ara": "/api/hakem-ara?ad=Ali&soyad=Demir",
            "enerji_fatura_borc_sorgula": "/api/enerji-fatura-borc-sorgula?tesisat=1001234567",
            "arac_bilgisi": "/api/arac-bilgisi?plaka=34ABC345"
        }
    })

# ==================== RENDER UYUMLU ÇALIŞTIRMA ====================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
