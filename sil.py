# Current Date and Time (UTC - YYYY-MM-DD HH:MM:SS formatted): 2025-05-22 16:25:10
# Current User's Login: tarihcituranx

import os
import json
import logging
import io
import time
import asyncio
import functools
import requests # D-Smart sorgusu için
import aiohttp # AlazNet sorguları için
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile, User as TelegramUser, CallbackQuery, Message
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    ConversationHandler,
    CallbackQueryHandler,
)
from telegram.constants import ParseMode, ChatAction
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.pdfbase.ttfonts import TTFont, TTFError
from reportlab.pdfbase import pdfmetrics
from aiohttp.client_exceptions import ClientResponseError
import datetime

DEJAVU_FONT_PATH = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
logger = logging.getLogger(__name__)
DEFAULT_FONT_NAME = 'Helvetica'
try:
    pdfmetrics.registerFont(TTFont('DejaVu', DEJAVU_FONT_PATH))
    DEFAULT_FONT_NAME = 'DejaVu'
    logger.info(f"DejaVu fontu başarıyla yüklendi: {DEJAVU_FONT_PATH}")
except TTFError as e_ttf:
    logger.error(f"DejaVu fontu yüklenemedi (TTFError: {e_ttf}). '{DEJAVU_FONT_PATH}' yolunu ve izinleri kontrol edin. Helvetica kullanılacak.")
except Exception as e_font_general:
    logger.error(f"Font yüklenirken genel hata ({DEJAVU_FONT_PATH}): {e_font_general}. Helvetica kullanılacak.")

TELEGRAM_TOKEN = "7790558183:AAFNcKnGcI_Lb3bwU1gVvZt4-2w0TA9mxo0" # Gerçek tokeninizle değiştirin
ADMIN_CHAT_ID = 7934417435 # Kendi admin chat ID'niz
BASE_URL_ALAZNET = "https://alaznet.com.tr/service/altyapi/"
SUPERONLINE_BASE_URL_DSMART = "https://www.dsmart.com.tr/api/v1/public/search/internet"
ALAZNET_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "*/*", "Accept-Language": "tr,en;q=0.9", "Referer": "https://alaznet.com.tr/service/altyapi/sayfa.php"}
DSMART_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json, text/plain, */*", "Accept-Language": "tr-TR,tr;q=0.9", "Connection": "keep-alive", "Referer": "https://www.dsmart.com.tr/internet-altyapi-sorgulama", "X-Requested-With": "XMLHttpRequest", "Origin": "https://www.dsmart.com.tr", "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "same-origin"}
DSMART_COOKIES = {"popupShown-s1227": "shown"}
cache = {}; CACHE_TTL = 86400
CHOOSE_METHOD, ASK_BBK, ASK_CITY_PLATE, CHOOSE_DISTRICT, CHOOSE_NEIGHBORHOOD, CHOOSE_STREET, CHOOSE_BUILDING, CHOOSE_APARTMENT, SHOW_RESULTS_AND_ACTIONS = range(9)

if not logger.handlers: logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

def escape_markdown_v2(text: str | None) -> str:
    if text is None: return ""
    text = str(text); escape_chars = r'_*[]()~`>#+-=|{}.!'
    return "".join(f"\\{char}" if char in escape_chars else char for char in text)

async def send_log_to_admin(context: ContextTypes.DEFAULT_TYPE, log_message: str):
    if ADMIN_CHAT_ID:
        try:
            for i in range(0, len(log_message), 4000): await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=log_message[i:i+4000], parse_mode=ParseMode.MARKDOWN_V2)
            logger.info(f"Log admin'e gönderildi ({ADMIN_CHAT_ID}).")
        except Exception as e:
            logger.error(f"Admin'e log gönderilemedi ({ADMIN_CHAT_ID}) (Hata: {e}) - Log: {log_message[:200]}")
            try:
                original_unescaped_log = log_message; [original_unescaped_log := original_unescaped_log.replace(f"\\{c}",c) for c in r'_*[]()~`>#+-=|{}.!']
                fallback_text = f"LOG GÖNDERİM HATASI ({str(e)})\n\nORİJİNAL LOG (ilk 1000 krk):\n{original_unescaped_log[:1000]}"
                for i in range(0, len(fallback_text), 4000): await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=fallback_text[i:i+4000])
                logger.info("Fallback log admin'e ham metin olarak gönderildi.")
            except Exception as fallback_e: logger.error(f"Admin'e fallback log gönderimi de başarısız oldu: {fallback_e}")

def format_admin_log_summary(query_type: str, alaz_input_id: str | None, alaz_data: dict, sol_bbk_used: str | None, sol_bina_kodu_used_for_api: str | None, superonline_data: dict, alaz_error: str = None, telegram_user: TelegramUser | None = None) -> str:
    e = escape_markdown_v2; current_utc_time_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    log_parts = [f"*{e('Sorgu Log Özeti')}*", f"{e('Tarih (UTC)')}: `{e(current_utc_time_str)}`"]
    if telegram_user: user_display_parts = [f"@{e(telegram_user.username)}" if telegram_user.username else "", e(telegram_user.first_name) if telegram_user.first_name else "", e(telegram_user.last_name) if telegram_user.last_name else ""]; user_info_display_str = " ".join(filter(None, user_display_parts)); log_parts.append(f"{e('Kullanıcı')}: `{user_info_display_str if user_info_display_str else e(str(telegram_user.id))}` {e('(')}{e('ID')}: `{e(str(telegram_user.id))}`{e(')')}")
    else: log_parts.append(f"{e('Kullanıcı')}: {e('Bilinmiyor')}")
    log_parts.extend([f"{e('Sorgu Tipi')}: `{e(query_type)}`", f"{e('AlazNet Giriş ID')}: `{e(alaz_input_id if alaz_input_id else 'Bilinmiyor')}`"])
    if alaz_error: log_parts.append(f"{e('AlazNet Durumu')}: *{e('HATA')}* {e('-')} `{e(alaz_error)}`")
    elif alaz_data.get("_error_"): log_parts.append(f"{e('AlazNet Durumu')}: *{e('HATA')}* {e('-')} `{e(alaz_data['_error_'])}`")
    elif alaz_data: log_parts.append(f"{e('AlazNet Durumu')}: `{e('Başarılı')}`"); log_parts.append(f"  {e('TT Tip')}: `{e(alaz_data.get('tip'))}` {e('Hız')}: `{e(alaz_data.get('hiz'))}` {e('Port')}: `{e(alaz_data.get('port'))}`")
    else: log_parts.append(f"{e('AlazNet Durumu')}: `{e('Veri Yok veya Sorgu Başarısız')}`")
    log_parts.append(f"{e('Süperonline Sorgusu İçin Kullanılan BBK')}: `{e(sol_bbk_used if sol_bbk_used else 'Yok')}`")
    log_parts.append(f"{e('Süperonline Sorgusu İçin API\'ye Gönderilen Bina Kodu')}: `{e(sol_bina_kodu_used_for_api if sol_bina_kodu_used_for_api != '' else 'Boş Gönderildi')}`")
    sol_data_field = superonline_data.get("data")
    if superonline_data.get("status") == "input_error": log_parts.append(f"{e('Süperonline Durumu')}: *{e('Atlandı')}* {e('-')} `{e(superonline_data.get('error', 'Giriş verisi eksik'))}`")
    elif "error" in superonline_data: sol_status_line = f"*{e('HATA')}* {e('-')} `{e(superonline_data.get('error', 'Bilinmeyen Süperonline hatası'))}`"; sol_status_line += f" {e('(')}{e('Status')}: `{e(superonline_data.get('status'))}`{e(')')}" if superonline_data.get('status', '') else ""; sol_status_line += f"\n  {e('Ham Yanıt (ilk 200 krk)')}: ```\n{e(str(superonline_data.get('raw_response'))[:200])}\n```" if superonline_data.get('raw_response', '') else ""; log_parts.append(f"{e('Süperonline Durumu')}: {sol_status_line}")
    elif isinstance(sol_data_field, dict) and "Message" in sol_data_field: log_parts.append(f"{e('Süperonline Durumu')}: *{e('API İç Hatası')}*"); log_parts.append(f"  {e('Mesaj')}: `{e(sol_data_field.get('Message'))}`"); log_parts.append(f"  {e('Kod')}: `{e(sol_data_field.get('Code'))}`"); log_parts.append(f"  {e('Ham Yanıt (ilk 200 krk)')}: ```\n{e(str(superonline_data.get('raw_response_success_preview'))[:200])}\n```" if superonline_data.get('raw_response_success_preview', '') else "")
    elif isinstance(sol_data_field, list) and sol_data_field: log_parts.append(f"{e('Süperonline Durumu')}: `{e('Başarılı (Veri Alındı)')}`"); sol_results = [f"  {e('-')} {e('Sağlayıcı')}: {e(item.get('provider'))}, {e('Hız')}: {e(item.get('maxSpeed'))}, {e('Teknoloji')}: {e(item.get('tech'))}, {e('Port')}: {e(str(item.get('portAvailable')))}" for item in sol_data_field if isinstance(item, dict)]; log_parts.append("\n".join(sol_results) if sol_results else "")
    elif isinstance(sol_data_field, list) and not sol_data_field: log_parts.append(f"{e('Süperonline Durumu')}: `{e('Başarılı (Altyapı/Port Yok veya Veri Formatsız)')}`")
    else: log_parts.append(f"{e('Süperonline Durumu')}: `{e('Bilinmeyen Durum / Yanıt Anlaşılamadı')}`"); log_parts.append(f"  {e('Ham Yanıt (ilk 200 krk)')}: ```\n{e(str(superonline_data.get('raw_response_success_preview', '') or superonline_data.get('raw_response', ''))[:200])}\n```" if (superonline_data.get('raw_response_success_preview', '') or superonline_data.get('raw_response', '')) else "")
    return "\n".join(filter(None, log_parts))

async def get_options_from_api_alaznet(endpoint, params):
    cache_key = f"alaznet_options:{endpoint}:{json.dumps(params, sort_keys=True)}"
    if cache_key in cache and time.time() - cache[cache_key]["timestamp"] < CACHE_TTL: logger.info(f"Önbellekten (alaznet_options): {cache_key}"); return cache[cache_key]["data"]
    start_time = time.time()
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(BASE_URL_ALAZNET + endpoint, params=params, headers=ALAZNET_HEADERS, timeout=12) as resp:
                resp.raise_for_status(); text = await resp.text(); soup = BeautifulSoup(text, "html.parser")
                options = [{"id": opt.get("value"), "text": opt.text.strip()} for opt in soup.find_all("option") if opt.get("value") and opt.get("value") != ""]
                logger.info(f"AlazNet seçenek ({endpoint}, {params}): {time.time() - start_time:.2f}s, sayı: {len(options)}")
                cache[cache_key] = {"data": options, "timestamp": time.time()}; return options
        except Exception as e: logger.error(f"AlazNet seçenek alma hatası ({endpoint}, {params}): {e}"); return []

async def do_final_query_alaznet(daire_id: str | None):
    if not daire_id: logger.warning("do_final_query_alaznet: daire_id None."); return {"_error_": "AlazNet için Daire ID sağlanmadı."}
    cache_key = f"alaznet_sorgu:{daire_id}"; start_time = time.time(); text_resp_for_log = ""
    if cache_key in cache and time.time() - cache[cache_key]["timestamp"] < CACHE_TTL: logger.info(f"Önbellekten (alaznet_sorgu): {cache_key}"); return cache[cache_key]["data"]
    async with aiohttp.ClientSession() as session:
        try:
            logger.info(f"AlazNet son sorgu (sorgu.php) daire_id: {daire_id}")
            async with session.get(BASE_URL_ALAZNET + "sorgu.php", params={"daire_id": daire_id}, headers=ALAZNET_HEADERS, timeout=15) as resp:
                text_resp_for_log = await resp.text(); resp.raise_for_status(); data = json.loads(text_resp_for_log)
                if isinstance(data, dict) and "aciklama" in data and isinstance(data["aciklama"], dict): ac_adreskodu = data["aciklama"].get("AdresKodu", {}); logger.info(f"AlazNet sorgu.php: BBolumKodu: {ac_adreskodu.get('Kod')}, BinaKodu: {ac_adreskodu.get('BinaKodu')} (ID: {daire_id})")
                cache[cache_key] = {"data": data, "timestamp": time.time()}; logger.info(f"AlazNet sorgu süresi (ID: {daire_id}): {time.time() - start_time:.2f}s"); return data
        except json.JSONDecodeError as e: logger.error(f"AlazNet JSON decode hatası (ID: {daire_id}): {e}. Yanıt: {text_resp_for_log[:500]}"); return {"_error_": f"AlazNet API yanıtı JSON değil: {str(e)}"}
        except ClientResponseError as e: logger.error(f"AlazNet ClientResponseError (ID: {daire_id}): Status {e.status}, Msg: {e.message}. Yanıt: {text_resp_for_log[:500]}"); return {"_error_": f"AlazNet API hatası: Status {e.status} - {e.message}"}
        except Exception as e: logger.error(f"AlazNet sorgu hatası (ID: {daire_id}): {e}. Yanıt: {text_resp_for_log[:500]}", exc_info=True); return {"_error_": f"AlazNet API genel hatası: {str(e)}"}

def superonline_query_sync(bbk_code: str | None, building_code_to_send_to_api: str | None) -> dict:
    start_time = time.time(); bbk_to_send = str(bbk_code) if bbk_code is not None else ""; building_code_final_for_api = str(building_code_to_send_to_api) if building_code_to_send_to_api is not None else ""
    logger.info(f"Süperonline (D-Smart API) senkron sorgu: BBK='{bbk_to_send}', BuildingCode='{building_code_final_for_api}'")
    if not bbk_to_send: logger.warning("Superonline için BBK boş."); return {"error": "BBK sağlanmadı", "status": "input_error"}
    files_payload = {'BBK': (None, bbk_to_send), 'BuildingCode': (None, building_code_final_for_api)}; response_text = ""
    try:
        response = requests.post(SUPERONLINE_BASE_URL_DSMART, files=files_payload, headers=DSMART_HEADERS, cookies=DSMART_COOKIES, timeout=25)
        response_text = response.text; logger.info(f"Süperonline (D-Smart API) ham yanıt (Status: {response.status_code}, BBK: {bbk_to_send}, BinaKodu: {building_code_final_for_api}): {response_text[:500]}")
        response.raise_for_status(); result = response.json()
        logger.info(f"Süperonline (D-Smart API) sorgu süresi (BBK: {bbk_to_send}): {time.time() - start_time:.2f}s"); result['raw_response_success_preview'] = response_text[:200]; return result
    except requests.exceptions.Timeout: logger.error(f"Süperonline (D-Smart API) zaman aşımı (BBK={bbk_to_send})"); return {"error": "Sorgu zaman aşımına uğradı", "status": "timeout", "raw_response": "Timeout"}
    except requests.exceptions.RequestException as e: status_code = e.response.status_code if e.response is not None else "N/A"; logger.error(f"Süperonline (D-Smart API) RequestException (BBK={bbk_to_send}): Status {status_code}, Err: {e}, Resp: {response_text[:500]}"); return {"error": f"API Bağlantı Hatası: {e}", "status": str(status_code), "raw_response": response_text}
    except json.JSONDecodeError:
        logger.error(f"Süperonline (D-Smart API) JSON değil (BBK={bbk_to_send}): {response_text[:500]}"); status_code_from_resp = response.status_code if 'response' in locals() and response is not None else "N/A_JSON_ERR"
        if status_code_from_resp == 200: return {"meta": {"code": 200, "message": "OK_BUT_NOT_JSON"}, "data": {"Message": "Bir Hata Oluştu (Yanıt JSON Değil)", "Code": "200_NOT_JSON"}, "raw_response": response_text, "status": "json_error_but_200"}
        return {"error": "Süperonline yanıtı anlaşılamadı (JSON değil)", "raw_response": response_text, "status": "json_error"}
    except Exception as e: logger.error(f"Süperonline (D-Smart API) genel hata (BBK={bbk_to_send}): {e}", exc_info=True); return {"error": f"Bilinmeyen hata: {str(e)}", "raw_response": response_text or "Genel hata", "status": "general_error"}

def check_superonline(data: dict) -> bool: sol_data_content = data.get("data"); return isinstance(sol_data_content, list) and any(isinstance(item, dict) and item.get("provider") == "SOL" and item.get("maxSpeed") == "1000 Mbps" and item.get("tech") == "Fiber" and item.get("portAvailable") is True for item in sol_data_content)
def get_value_from_veriler_list(veriler_list, target_name): return next((item.get("value") for item in veriler_list if isinstance(item, dict) and item.get("name") == target_name), None) if veriler_list else None

def detect_is_fttc(data: dict | None) -> str | None: # UnboundLocalError için düzeltildi
    if not data: 
        return None
    detay = data.get("detay", {}) # <--- EKLENEN/DÜZELTİLEN SATIR
    if not isinstance(detay, dict): 
        return None
    
    is_fttc_val = (
        get_value_from_veriler_list(detay.get("VdslVeriler"), "ISFTTC") or
        get_value_from_veriler_list(detay.get("Veriler"), "ISFTTC") or 
        None
    )
    return is_fttc_val if is_fttc_val and str(is_fttc_val).strip().lower() not in ["yok", ""] else None

def generate_pdf_report(alaz_data: dict | None, alaz_id_display: str | None, context: ContextTypes.DEFAULT_TYPE, detay_mode=False) -> io.BytesIO:
    start_time = time.time(); buffer = io.BytesIO(); doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=30, rightMargin=30, topMargin=30, bottomMargin=30); elements = []
    styles = getSampleStyleSheet(); styleN = ParagraphStyle('NormalWithFont', fontName=DEFAULT_FONT_NAME, fontSize=9, parent=styles['Normal'], leading=12); styleB = ParagraphStyle('Heading2WithFont', fontName=DEFAULT_FONT_NAME, fontSize=12, spaceAfter=6, parent=styles['Heading2'], leading=14); styleH3 = ParagraphStyle('Heading3WithFont', fontName=DEFAULT_FONT_NAME, fontSize=10, spaceBefore=6, spaceAfter=4, parent=styles['Heading3'], leading=12); styleSmall = ParagraphStyle('SmallWithFont', fontName=DEFAULT_FONT_NAME, fontSize=8, parent=styles['Normal'], leading=10)
    if not alaz_data or "_error_" in alaz_data: elements.append(Paragraph(f"Türk Telekom Altyapı Sorgu Sonucu (ID: {alaz_id_display or 'Bilinmiyor'})", styleB)); elements.append(Paragraph(f"AlazNet Hata: {str(alaz_data['_error_'] if alaz_data and '_error_' in alaz_data else 'Veri alınamadı.')}", styleN))
    else:
        detay_pdf = alaz_data.get("detay", {}) if isinstance(alaz_data.get("detay"), dict) else {}; api_main_tip, api_main_hiz = str(alaz_data.get("tip", "Bilinmiyor")), str(alaz_data.get("hiz", "N/A"))
        full_adres_raw = alaz_data.get("full_adres") or detay_pdf.get("AcikAdres"); full_adres = str(full_adres_raw.get("Adres", full_adres_raw) if isinstance(full_adres_raw, dict) else (full_adres_raw if isinstance(full_adres_raw, str) else "Adres bilgisi yok."))
        fttx1gb = get_value_from_veriler_list(detay_pdf.get("FiberVeriler"), "FTTX1GB"); tahmini_hiz_display = f"{api_main_hiz} Mbps" if api_main_hiz.isdigit() else api_main_hiz
        if api_main_tip == "FIBER" and str(fttx1gb) == "-2": tahmini_hiz_display = "100 Mbps*"
        elements.append(Paragraph(f"Türk Telekom {'Detaylı ' if detay_mode else ''}Altyapı Sorgu Sonucu", styleB)); elements.append(Spacer(1, 6)); elements.append(Paragraph(f"<b>AlazNet Giriş ID:</b> {str(alaz_id_display or 'Bilinmiyor')}", styleN)); elements.append(Paragraph(f"<b>Adres:</b> {full_adres}", styleN)); elements.append(Spacer(1, 8))
        tt_rows_data = [["Aktif Altyapı Türü", api_main_tip], ["Tahmini Alınabilir Hız", tahmini_hiz_display], ["Boş Port Durumu", "Var" if str(alaz_data.get("port", "")) == "1" else ("Yok" if str(alaz_data.get("port", "")) == "0" else "Bilinmiyor")]]
        if detay_mode:
            get_val_pdf = lambda lst_name, key: get_value_from_veriler_list(detay_pdf.get(lst_name), key)
            mudurluk_adi = get_val_pdf("FiberVeriler", "SNTRLMDA") or get_val_pdf("VdslVeriler", "SNTRLMDA") or get_val_pdf("Veriler", "SNTRLMDA") or "N/A"
            santral_adi, santral_mesafe = detay_pdf.get("SantralAdi", "N/A"), str(detay_pdf.get("SantralMesafe", "N/A"))
            tt_rows_data.extend([["Müdürlük Adı", mudurluk_adi], ["Santral Adı", santral_adi]]);
            if santral_mesafe not in ["N/A", "0", "None", ""]: tt_rows_data.append(["Santral Mesafesi", f"{santral_mesafe} m"])
            if api_main_tip == "FIBER" and str(detay_pdf.get("FiberDurum")) == "1": fttx_altyapi_turu = "Fiber"; fttx_altyapi_turu = "FTTH (Eve Kadar Fiber)" if str(fttx1gb) == "1" else ("FTTB-ETH (Binaya Kadar Fiber)*" if str(fttx1gb) == "-2" else fttx_altyapi_turu); tt_rows_data.append(["FTTX Altyapı Türü", fttx_altyapi_turu])
            is_emri_display = str(get_val_pdf("FiberVeriler", "ACKISEMRI") or get_val_pdf("VdslVeriler", "ACKISEMRI") or get_val_pdf("Veriler", "ACKISEMRI") or "YOK").strip(); tt_rows_data.append(["Açık İş Emri", "YOK" if not is_emri_display or is_emri_display == '| |' else is_emri_display])
        is_fttc_val_pdf = detect_is_fttc(alaz_data) # detect_is_fttc düzeltildi ve çağrılıyor
        if api_main_tip in ["VDSL", "ADSL"] and is_fttc_val_pdf: tt_rows_data.append(["Saha Dolabı Bilgisi (FTTC/B)", is_fttc_val_pdf])
        tt_table = Table([ [Paragraph("Alan", styleN), Paragraph("Değer", styleN)] ] + [[Paragraph(str(c), styleN) for c in row] for row in tt_rows_data], hAlign='LEFT', colWidths=[160, None])
        tt_table.setStyle(TableStyle([('FONTNAME', (0,0), (-1,-1), DEFAULT_FONT_NAME), ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#e0e0e0")), ('TEXTCOLOR', (0,0), (-1,0), colors.black), ('ALIGN', (0,0), (-1,-1), 'LEFT'), ('VALIGN', (0,0), (-1,-1), 'MIDDLE'), ('GRID', (0,0), (-1,-1), 0.7, colors.grey), ('LEFTPADDING', (0,0), (-1,-1), 5), ('RIGHTPADDING', (0,0), (-1,-1), 5), ('TOPPADDING', (0,0), (-1,-1), 3), ('BOTTOMPADDING', (0,0), (-1,-1), 3)])); elements.append(tt_table)
        if api_main_tip in ["VDSL", "ADSL"] and is_fttc_val_pdf: elements.extend([Spacer(1, 6), Paragraph(f"<i>Saha Dolabı (FTTC/B) Notu: {is_fttc_val_pdf}</i>", styleSmall)])
    elements.extend([Spacer(1, 16), Paragraph("Süperonline Altyapı Sonuçları", styleB), Spacer(1,6)]); sol_data_ctx = context.user_data.get('superonline_data', {}); sol_rows_content = []
    sol_rows_content.append([Paragraph("Sorgulanan BBK (SOL)", styleN), Paragraph(str(context.user_data.get('sol_queried_bbk', 'Bilinmiyor')), styleN)])
    sol_rows_content.append([Paragraph("Sorgulanan Bina Kodu (SOL API)", styleN), Paragraph(str(context.user_data.get('sol_sent_bina_kodu_for_api_display', 'Yok/Kullanılmadı')), styleN)])
    sol_data_field_pdf = sol_data_ctx.get("data")
    if sol_data_ctx.get("status") == "input_error": sol_rows_content.append([Paragraph("Durum", styleN), Paragraph(f"Atlandı: {str(sol_data_ctx.get('error', 'Giriş verisi eksik'))}", styleN)])
    elif "error" in sol_data_ctx: sol_rows_content.append([Paragraph("Durum", styleN), Paragraph(f"Hata: {str(sol_data_ctx.get('error'))} (Status: {str(sol_data_ctx.get('status', 'N/A'))})", styleN)])
    elif isinstance(sol_data_field_pdf, dict) and "Message" in sol_data_field_pdf: sol_rows_content.append([Paragraph("Durum", styleN), Paragraph(f"API Yanıtı: {str(sol_data_field_pdf.get('Message', 'Bilinmeyen API Hatası'))} (Kod: {str(sol_data_field_pdf.get('Code', 'N/A'))})", styleN)])
    elif isinstance(sol_data_field_pdf, list) and sol_data_field_pdf: [sol_rows_content.append([Paragraph(f"Sağlayıcı: {str(item_sol.get('provider','?'))}", styleN), Paragraph(f"{str(item_sol.get('maxSpeed','?'))} ({str(item_sol.get('tech','?'))}, Port: {'Mevcut' if item_sol.get('portAvailable') else 'Yok'})", styleN)]) for item_sol in sol_data_field_pdf if isinstance(item_sol, dict)]
    elif isinstance(sol_data_field_pdf, list) and not sol_data_field_pdf: sol_rows_content.append([Paragraph("Durum", styleN), Paragraph("Altyapı bulunamadı (Dolu port veya uygun hizmet yok).", styleN)])
    else: sol_rows_content.append([Paragraph("Durum", styleN), Paragraph(str(sol_data_ctx.get('raw_response_success_preview', '') or sol_data_ctx.get('raw_response', '') or "Bilgi yok / Altyapı bulunamadı.")[:200], styleN)])
    sol_table = Table([ [Paragraph("Alan", styleN), Paragraph("Değer", styleN)] ] + sol_rows_content, hAlign='LEFT', colWidths=[160, None])
    sol_table.setStyle(TableStyle([('FONTNAME', (0,0), (-1,-1), DEFAULT_FONT_NAME), ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#e0e0e0")), ('TEXTCOLOR', (0,0), (-1,0), colors.black), ('ALIGN', (0,0), (-1,-1), 'LEFT'), ('VALIGN', (0,0), (-1,-1), 'MIDDLE'), ('GRID', (0,0), (-1,-1), 0.7, colors.grey), ('LEFTPADDING', (0,0), (-1,-1), 5), ('RIGHTPADDING', (0,0), (-1,-1), 5), ('TOPPADDING', (0,0), (-1,-1), 3), ('BOTTOMPADDING', (0,0), (-1,-1), 3)])); elements.append(sol_table)
    elements.extend([Spacer(1, 16), Paragraph("<b>NOTLAR:</b>", styleH3)])
    notlar_list = [ # PDF notlarına ek yapıldı
        "Bu rapordaki veriler Türk Telekom (AlazNet) ve Süperonline (DSmart API) üzerinden alınmıştır ve sadece bilgilendirme amaçlıdır.",
        "Verilerin güncelliği ve doğruluğu garanti edilmez. Kesin bilgi için ilgili servis sağlayıcıları ile iletişime geçiniz.",
        "Süperonline (D-Smart API) sorgulaması bazen D-Smart kaynaklı olarak hatalı veya eksik sonuç verebilir.",
        "* FTTB-ETH (Binaya Kadar Fiber): Bu tür altyapıda hız, bina içi tesisatın durumuna ve kalitesine bağlı olarak 100 Mbps ile sınırlı olabilir.",
        f"PDF oluşturulurken kullanılan font: {DEFAULT_FONT_NAME}"
    ]
    for note in notlar_list: elements.append(Paragraph(f"• {note}", styleSmall, bulletText='•')); elements.append(Spacer(1, 2))
    doc.build(elements); buffer.seek(0); logger.info(f"PDF oluşturma süresi: {time.time() - start_time:.2f}s (Font: {DEFAULT_FONT_NAME})"); return buffer

async def async_generate_pdf(data, bbk_code_display, context, detay_mode): loop = asyncio.get_event_loop(); return await loop.run_in_executor(None, generate_pdf_report, data, bbk_code_display, context, detay_mode)
TURKISH_CITY_PLATE_MAP = {"adana":"01", "adiyaman":"02", "afyonkarahisar":"03", "ağri":"04", "amasya":"05", "ankara":"06", "antalya":"07", "artvin":"08", "aydin":"09", "balikesir":"10", "bilecik":"11", "bingöl":"12", "bitlis":"13", "bolu":"14", "burdur":"15", "bursa":"16", "çanakkale":"17", "çankiri":"18", "çorum":"19", "denizli":"20", "diyarbakir":"21", "edirne":"22", "elaziğ":"23", "erzincan":"24", "erzurum":"25", "eskişehir":"26", "gaziantep":"27", "giresun":"28", "gümüşhane":"29", "hakkari":"30", "hatay":"31", "isparta":"32", "mersin":"33", "istanbul":"34", "izmir":"35", "kars":"36", "kastamonu":"37", "kayseri":"38", "kirklareli":"39", "kirşehir":"40", "kocaeli":"41", "konya":"42", "kütahya":"43", "malatya":"44", "manisa":"45", "kahramanmaraş":"46", "mardin":"47", "muğla":"48", "muş":"49", "nevşehir":"50", "niğde":"51", "ordu":"52", "rize":"53", "sakarya":"54", "samsun":"55", "siirt":"56", "sinop":"57", "sivas":"58", "tekirdağ":"59", "tokat":"60", "trabzon":"61", "tunceli":"62", "şanliurfa":"63", "uşak":"64", "van":"65", "yozgat":"66", "zonguldak":"67", "aksaray":"68", "bayburt":"69", "karaman":"70", "kirikkale":"71", "batman":"72", "şirnak":"73", "bartin":"74", "ardahan":"75", "iğdir":"76", "yalova":"77", "karabük":"78", "kilis":"79", "osmaniye":"80", "düzce":"81"}
def normalize_city_name(name: str) -> str: name = name.lower(); replacements = {'ç':'c','ğ':'g','ı':'i','ö':'o','ş':'s','ü':'u','.':'',',':''}; return "".join(replacements.get(c,c) for c in name).strip()
NORMALIZED_CITY_PLATE_MAP = {normalize_city_name(k): v for k, v in TURKISH_CITY_PLATE_MAP.items()}
def get_plate_from_city_name(city_name_input: str) -> str | None: return NORMALIZED_CITY_PLATE_MAP.get(normalize_city_name(city_name_input))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: return await start_new_query(update, context, edit_message=None, force_new_message=True)
async def choose_method_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    if query.data == "query_bbk": await query.edit_message_text(escape_markdown_v2("Lütfen BBK (Bağımsız Bölüm Kodu) girin:"), parse_mode=ParseMode.MARKDOWN_V2); return ASK_BBK
    await query.edit_message_text(escape_markdown_v2("Lütfen plaka numarası (örn: 06) veya il adı girin:"), parse_mode=ParseMode.MARKDOWN_V2); return ASK_CITY_PLATE

async def receive_bbk(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    bbk_input_by_user = update.message.text.strip()
    if not bbk_input_by_user or not bbk_input_by_user.isdigit(): await update.message.reply_text(escape_markdown_v2("Geçersiz BBK formatı! Sadece rakam girin."), parse_mode=ParseMode.MARKDOWN_V2); return ASK_BBK
    context.user_data['alaznet_queried_id'] = bbk_input_by_user; await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    progress_message = await update.message.reply_text(escape_markdown_v2(f"BBK {bbk_input_by_user} ile AlazNet sorgulanıyor..."), parse_mode=ParseMode.MARKDOWN_V2)
    alaz_data = await do_final_query_alaznet(bbk_input_by_user); context.user_data['api_data'] = alaz_data; alaz_error_message = None; ac_adreskodu = {} # Tanımlama
    sol_bbk_to_use_for_api = bbk_input_by_user; sol_bina_kodu_to_use_for_api = ""
    if "_error_" in alaz_data: alaz_error_message = alaz_data["_error_"]; await progress_message.edit_text(escape_markdown_v2(f"AlazNet hatası: {alaz_error_message}. Süperonline BBK {bbk_input_by_user} ile sorgulanacak (Bina Kodu olmadan)."), parse_mode=ParseMode.MARKDOWN_V2)
    else:
        await progress_message.edit_text(escape_markdown_v2("AlazNet tamamlandı, Süperonline için veriler hazırlanıyor..."), parse_mode=ParseMode.MARKDOWN_V2)
        if isinstance(alaz_data, dict) and "aciklama" in alaz_data and isinstance(alaz_data["aciklama"], dict): ac_adreskodu = alaz_data["aciklama"].get("AdresKodu", {});
        if isinstance(ac_adreskodu, dict) and ac_adreskodu.get("BinaKodu"): logger.info(f"AlazNet'ten önerilen BinaKodu: {ac_adreskodu.get('BinaKodu')} (API'ye '' gönderilecek).")
    context.user_data.update({'sol_queried_bbk': sol_bbk_to_use_for_api, 'sol_queried_bina_kodu_for_api': sol_bina_kodu_to_use_for_api, 'sol_sent_bina_kodu_for_api_display': "Yok/Boş Gönderildi"})
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    await progress_message.edit_text(escape_markdown_v2(f"AlazNet tamam. Süperonline (D-Smart API) sorgulanıyor (BBK: {sol_bbk_to_use_for_api}, Bina Kodu: Boş)..."), parse_mode=ParseMode.MARKDOWN_V2)
    superonline_data = await asyncio.get_event_loop().run_in_executor(None, functools.partial(superonline_query_sync, bbk_code=sol_bbk_to_use_for_api, building_code_to_send_to_api=sol_bina_kodu_to_use_for_api))
    context.user_data['superonline_data'] = superonline_data
    if check_superonline(superonline_data): await update.message.reply_text(escape_markdown_v2("*Süperonline Fiber görünüyor!* (Detaylar PDF'te)"), parse_mode=ParseMode.MARKDOWN_V2)
    elif isinstance(superonline_data.get("data"), dict) and superonline_data["data"].get("Message") == "Bir Hata Oluştu": await update.message.reply_text(escape_markdown_v2("Süperonline API hatası: \"Bir Hata Oluştu\"."), parse_mode=ParseMode.MARKDOWN_V2)
    elif "error" in superonline_data and superonline_data.get("status") != "input_error": await update.message.reply_text(escape_markdown_v2(f"Süperonline hatası: {superonline_data.get('error')} (Status: {superonline_data.get('status')})."), parse_mode=ParseMode.MARKDOWN_V2)
    elif superonline_data.get("status") == "input_error": await update.message.reply_text(escape_markdown_v2(f"Süperonline sorgusu atlandı: {superonline_data.get('error')}."), parse_mode=ParseMode.MARKDOWN_V2)
    log_summary = format_admin_log_summary("BBK", bbk_input_by_user, alaz_data, sol_bbk_to_use_for_api, sol_bina_kodu_to_use_for_api, superonline_data, alaz_error_message, telegram_user=update.effective_user)
    await send_log_to_admin(context, log_summary); await send_summary_and_buttons(update, context, alaz_data, bbk_input_by_user); return SHOW_RESULTS_AND_ACTIONS

async def receive_city_plate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_input = update.message.text.strip(); plaka = None; il_adi_display = user_input.capitalize(); user_input_norm = normalize_city_name(user_input)
    if user_input_norm.isdigit() and 1 <= len(user_input_norm) <= 2: plaka = user_input_norm.zfill(2); il_adi_display = next((orig_city.capitalize() for orig_city, orig_plate in TURKISH_CITY_PLATE_MAP.items() if orig_plate == plaka), f"Plaka {plaka}")
    else: plaka = get_plate_from_city_name(user_input_norm); il_adi_display = next((orig_city.capitalize() for orig_city, orig_plate in TURKISH_CITY_PLATE_MAP.items() if orig_plate == plaka), il_adi_display) if plaka else il_adi_display
    if not plaka: await update.message.reply_text(escape_markdown_v2("Geçersiz plaka veya il adı!"), parse_mode=ParseMode.MARKDOWN_V2); return ASK_CITY_PLATE
    context.user_data.update({'plaka': plaka, 'il_adi_display_raw': il_adi_display}); logger.info(f"İl/Plaka: {il_adi_display} ({plaka})")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING); return await adres_adim(update, context, 1)

async def adres_adim(update_or_query: Update | CallbackQuery, context: ContextTypes.DEFAULT_TYPE, adim: int):
    user_data = context.user_data; params_map = {"city": user_data.get('plaka'), "district": user_data.get('ilce_id'), "neighborhoods": user_data.get('mahalle_id'), "street": user_data.get('sokak_id'), "building": user_data.get('bina_id')}
    adim_cfg = [("district.php", "city", "İlçe", "district_", CHOOSE_DISTRICT), ("neighborhoods.php", "district", "Mahalle", "neighborhoods_", CHOOSE_NEIGHBORHOOD), ("street.php", "neighborhoods", "Sokak/Cadde", "street_", CHOOSE_STREET), ("building.php", "street", "Bina", "building_", CHOOSE_BUILDING), ("home.php", "building", "Daire (BBK)", "home_", CHOOSE_APARTMENT)]
    chat_id = update_or_query.effective_chat.id if isinstance(update_or_query, Update) and update_or_query.effective_chat else (update_or_query.message.chat_id if isinstance(update_or_query, CallbackQuery) and update_or_query.message else user_data.get('last_chat_id'))
    if not chat_id: logger.error(f"adres_adim: Chat ID yok (adım {adim})."); return ConversationHandler.END
    if not (0 < adim <= len(adim_cfg)): logger.error(f"Geçersiz adres adımı: {adim}"); await context.bot.send_message(chat_id, escape_markdown_v2("Hata: Geçersiz adım."), parse_mode=ParseMode.MARKDOWN_V2); return ConversationHandler.END
    endpoint, param_key_api, label, cb_prefix, current_state = adim_cfg[adim - 1]; api_param_value = params_map.get(param_key_api)
    if api_param_value is None and adim > 1: logger.warning(f"Adres adımı {adim} eksik param: '{param_key_api}'"); msg_err = escape_markdown_v2("Önceki adım tamamlanmalı."); await (update_or_query.message.edit_text(msg_err, parse_mode=ParseMode.MARKDOWN_V2) if isinstance(update_or_query, CallbackQuery) and update_or_query.message else context.bot.send_message(chat_id, text=msg_err, parse_mode=ParseMode.MARKDOWN_V2)); return user_data.get('adres_adim_state', CHOOSE_METHOD)
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING); options = await get_options_from_api_alaznet(endpoint, {param_key_api: api_param_value}); user_data['last_options'] = options
    message_to_act_on = update_or_query.message if isinstance(update_or_query, CallbackQuery) else (update_or_query.message if isinstance(update_or_query, Update) else None)
    is_editing = isinstance(update_or_query, CallbackQuery) and message_to_act_on is not None
    if not options:
        empty_msg = escape_markdown_v2(f"{label} bilgisi alınamadı." + (" Geri gidin." if endpoint != "home.php" else " Bu bina için daire (BBK) bulunamadı. Farklı bina seçin veya /start."))
        nav_btns_empty = [InlineKeyboardButton("🏠 Ana Menü", callback_data="adres_ana_menu")];
        if adim > 1: nav_btns_empty.insert(0, InlineKeyboardButton("⬅️ Geri", callback_data="adres_geri"))
        reply_markup_empty = InlineKeyboardMarkup([nav_btns_empty])
        if is_editing: await message_to_act_on.edit_text(empty_msg, reply_markup=reply_markup_empty, parse_mode=ParseMode.MARKDOWN_V2)
        else: await context.bot.send_message(chat_id=chat_id, text=empty_msg, reply_markup=reply_markup_empty, parse_mode=ParseMode.MARKDOWN_V2)
        user_data.update({'adres_adim': adim, 'adres_adim_state': current_state}); return current_state
    keyboard = [ [InlineKeyboardButton(opt['text'][:35] + ('...' if len(opt['text']) > 38 else ''), callback_data=f"{cb_prefix}{opt['id']}")] for opt in options]
    nav_btns = [InlineKeyboardButton("🏠 Ana Menü", callback_data="adres_ana_menu")];
    if adim > 1: nav_btns.insert(0, InlineKeyboardButton("⬅️ Geri", callback_data="adres_geri"))
    keyboard.append(nav_btns); reply_markup = InlineKeyboardMarkup(keyboard)
    breadcrumb_parts_raw = [user_data.get(key) for key in ['il_adi_display_raw', 'ilce_text_raw', 'mahalle_text_raw', 'sokak_text_raw', 'bina_text_raw'][:adim] if user_data.get(key)]
    breadcrumb_final_str = escape_markdown_v2(" > ").join(map(escape_markdown_v2, breadcrumb_parts_raw)); label_escaped = escape_markdown_v2(label.lower())
    msg_text = f"{breadcrumb_final_str}\nLütfen bir {label_escaped} seçin:" if breadcrumb_final_str else f"Lütfen bir {label_escaped} seçin:"
    if is_editing: await message_to_act_on.edit_text(msg_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2)
    else: await context.bot.send_message(chat_id=chat_id, text=msg_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2)
    user_data.update({'adres_adim': adim, 'adres_adim_state': current_state}); logger.info(f"Adres adımı {adim} ({label}) gösterildi. State: {current_state}"); return current_state

async def adres_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query: logger.warning("adres_callback_handler: update.callback_query None."); return await start_new_query(update, context, edit_message=update.effective_message if hasattr(update, 'effective_message') else None, force_new_message=True)
    
    await query.answer()
    cb_data = query.data
    user_data = context.user_data
    current_adim = user_data.get('adres_adim', 1)
    logger.info(f"adres_callback_handler: Gelen veri='{cb_data}', mevcut_adim={current_adim}")
    
    chat_id = query.message.chat_id if query.message else user_data.get('last_chat_id')
    if not chat_id: logger.error("adres_callback_handler: Chat ID yok!"); return ConversationHandler.END

    if cb_data == "adres_geri":
        if current_adim > 1:
            keys_to_clear_map = {2: ('ilce_id', 'ilce_text_raw'), 3: ('mahalle_id', 'mahalle_text_raw'), 4: ('sokak_id', 'sokak_text_raw'), 5: ('bina_id', 'bina_text_raw'), 6: ('daire_id', None)}
            if current_adim in keys_to_clear_map: 
                id_key, text_key_raw = keys_to_clear_map[current_adim]
                user_data.pop(id_key, None)
                if text_key_raw: user_data.pop(text_key_raw, None)
            for key_to_pop in ['sol_queried_bbk', 'sol_queried_bina_kodu_for_api', 'sol_sent_bina_kodu_for_api_display', 'alaznet_queried_id']: user_data.pop(key_to_pop, None)
            logger.info(f"Geri: Adım {current_adim} -> {current_adim-1}")
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            return await adres_adim(query, context, current_adim - 1)
        else:
            return await start_new_query(update, context, edit_message=query.message, force_new_message=False)

    elif cb_data == "adres_ana_menu": # elif olarak değiştirildi
        return await start_new_query(update, context, edit_message=query.message, force_new_message=False)

    else: # Diğer tüm callback_data durumları (district_, home_ vb.)
        next_adim_to_load = -1
        last_options = user_data.get('last_options', [])
        selection_prefixes = {"district_": ('ilce_id', 'ilce_text_raw', 2), "neighborhoods_": ('mahalle_id', 'mahalle_text_raw', 3), "street_": ('sokak_id', 'sokak_text_raw', 4), "building_": ('bina_id', 'bina_text_raw', 5)}
        
        processed_selection = False
        for prefix, (id_key, text_key_raw, next_step_no) in selection_prefixes.items():
            if cb_data.startswith(prefix):
                selected_id_value = cb_data.split(prefix)[1]
                user_data[id_key] = selected_id_value
                user_data[text_key_raw] = next((opt['text'] for opt in last_options if opt['id'] == selected_id_value), selected_id_value)
                next_adim_to_load = next_step_no
                logger.info(f"Seçim: {prefix}{selected_id_value} ({user_data[text_key_raw]}). Sonraki: {next_adim_to_load}")
                processed_selection = True
                break
        
        if cb_data.startswith("home_"):
            processed_selection = True
            alaznet_selected_daire_id = cb_data.split("home_")[1]
            
            if not alaznet_selected_daire_id:
                logger.error("Hata: home_ callback boş daire ID.")
                if query.message and chat_id:
                    await context.bot.send_message(chat_id=chat_id, text=escape_markdown_v2("Daire seçimi hatası (ID alınamadı)."), parse_mode=ParseMode.MARKDOWN_V2)
                return user_data.get('adres_adim_state', CHOOSE_APARTMENT)
            
            user_data.update({'daire_id': alaznet_selected_daire_id, 'alaznet_queried_id': alaznet_selected_daire_id})
            logger.info(f"Daire seçildi (AlazNet ID): {alaznet_selected_daire_id}. AlazNet sorgusu.")
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            
            edited_progress_msg: Message | None = None
            progress_text_initial = escape_markdown_v2(f"Daire ID {alaznet_selected_daire_id} için AlazNet sorgulanıyor...")
            if query.message:
                try: edited_progress_msg = await query.edit_message_text(text=progress_text_initial, parse_mode=ParseMode.MARKDOWN_V2)
                except Exception as e_edit: logger.error(f"Daire seçimi mesaj düzenleme hatası (1): {e_edit}"); await context.bot.send_message(chat_id, text=progress_text_initial, parse_mode=ParseMode.MARKDOWN_V2)
            else: await context.bot.send_message(chat_id, text=progress_text_initial, parse_mode=ParseMode.MARKDOWN_V2)
            
            alaz_data = await do_final_query_alaznet(alaznet_selected_daire_id)
            user_data['api_data'] = alaz_data; alaz_error_for_admin_log = None; ac_adreskodu = {} # Tanımlama
            sol_bbk_to_use_for_api = None; sol_bina_kodu_to_use_for_api = ""; msg_after_alaz = ""
            
            if "_error_" in alaz_data:
                alaz_error_for_admin_log = alaz_data["_error_"]; sol_bbk_to_use_for_api = alaznet_selected_daire_id
                msg_after_alaz = escape_markdown_v2(f"AlazNet hatası: {alaz_error_for_admin_log}. Süperonline BBK {sol_bbk_to_use_for_api} ile sorgulanacak (Bina Kodu olmadan).")
            else:
                msg_after_alaz = escape_markdown_v2("AlazNet tamamlandı, Süperonline için veriler hazırlanıyor...")
                if isinstance(alaz_data, dict) and "aciklama" in alaz_data and isinstance(alaz_data["aciklama"], dict):
                    ac_adreskodu = alaz_data["aciklama"].get("AdresKodu", {}); bbolum_kodu_from_alaz = ac_adreskodu.get("Kod")
                    sol_bbk_to_use_for_api = str(bbolum_kodu_from_alaz) if bbolum_kodu_from_alaz else alaznet_selected_daire_id
                    if ac_adreskodu.get("BinaKodu"): logger.info(f"AlazNet'ten SOL için BBK: {sol_bbk_to_use_for_api}. Önerilen BinaKodu: {ac_adreskodu.get('BinaKodu')} (API'ye '' gönderilecek).")
                else: sol_bbk_to_use_for_api = alaznet_selected_daire_id
                
            if edited_progress_msg:
                try: await edited_progress_msg.edit_text(text=msg_after_alaz, parse_mode=ParseMode.MARKDOWN_V2)
                except Exception as e_edit2: logger.error(f"Daire seçimi mesaj düzenleme hatası (2): {e_edit2}"); await context.bot.send_message(chat_id, text=msg_after_alaz, parse_mode=ParseMode.MARKDOWN_V2)
            elif query.message: await context.bot.send_message(chat_id, text=msg_after_alaz, parse_mode=ParseMode.MARKDOWN_V2)
            
            user_data.update({'sol_queried_bbk': sol_bbk_to_use_for_api, 'sol_queried_bina_kodu_for_api': sol_bina_kodu_to_use_for_api, 'sol_sent_bina_kodu_for_api_display': "Yok/Boş Gönderildi"})
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            msg_before_sol = escape_markdown_v2(f"AlazNet tamam. Süperonline (D-Smart API) sorgulanıyor (BBK: {sol_bbk_to_use_for_api or 'Yok'}, Bina Kodu: Boş)...")
            
            if edited_progress_msg:
                try: await edited_progress_msg.edit_text(text=msg_before_sol, parse_mode=ParseMode.MARKDOWN_V2)
                except Exception as e_edit3: logger.error(f"Daire seçimi mesaj düzenleme hatası (3): {e_edit3}"); await context.bot.send_message(chat_id, text=msg_before_sol, parse_mode=ParseMode.MARKDOWN_V2)
            elif query.message: await context.bot.send_message(chat_id, text=msg_before_sol, parse_mode=ParseMode.MARKDOWN_V2)
            
            superonline_data = await asyncio.get_event_loop().run_in_executor(None, functools.partial(superonline_query_sync, bbk_code=sol_bbk_to_use_for_api, building_code_to_send_to_api=sol_bina_kodu_to_use_for_api))
            user_data['superonline_data'] = superonline_data
            
            if check_superonline(superonline_data): await context.bot.send_message(chat_id, text=escape_markdown_v2("*Süperonline Fiber görünüyor!* (Detaylar PDF'te)"), parse_mode=ParseMode.MARKDOWN_V2)
            elif isinstance(superonline_data.get("data"), dict) and superonline_data["data"].get("Message") == "Bir Hata Oluştu": await context.bot.send_message(chat_id, text=escape_markdown_v2("Süperonline API hatası: \"Bir Hata Oluştu\"."), parse_mode=ParseMode.MARKDOWN_V2)
            elif "error" in superonline_data and superonline_data.get("status") != "input_error": await context.bot.send_message(chat_id, text=escape_markdown_v2(f"Süperonline hatası: {superonline_data.get('error')} (Status: {superonline_data.get('status')})."), parse_mode=ParseMode.MARKDOWN_V2)
            elif superonline_data.get("status") == "input_error": await context.bot.send_message(chat_id, text=escape_markdown_v2(f"Süperonline sorgusu atlandı: {superonline_data.get('error')}."), parse_mode=ParseMode.MARKDOWN_V2)
            
            log_sum = format_admin_log_summary("Adres", alaznet_selected_daire_id, alaz_data, sol_bbk_to_use_for_api, sol_bina_kodu_to_use_for_api, superonline_data, alaz_error_for_admin_log, telegram_user=query.from_user)
            await send_log_to_admin(context, log_sum)
            await send_summary_and_buttons(query, context, alaz_data, alaznet_selected_daire_id)
            return SHOW_RESULTS_AND_ACTIONS

        if next_adim_to_load != -1: # Bu, district_, neighborhoods_ vb. seçimlerinden sonra çalışır
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            return await adres_adim(query, context, next_adim_to_load)
        
        if not processed_selection: # Eğer cb_data hiçbir koşula uymadıysa
            logger.warning(f"adres_callback_handler: Bilinmeyen veya işlenemeyen callback_data='{cb_data}'. Ana menüye yönlendiriliyor.")
        
        return await start_new_query(update, context, edit_message=query.message, force_new_message=True) # Fallback

async def send_summary_and_buttons(update_or_query, context, alaz_net_data, alaz_id_display):
    e = escape_markdown_v2; keyboard = [[InlineKeyboardButton(e("Detaylı PDF İndir"), callback_data="download_pdf_detay")], [InlineKeyboardButton(e("Ham JSON İndir"), callback_data="download_json")], [InlineKeyboardButton(e("🏠 Ana Menü (Yeni Sorgu)"), callback_data="new_query")]]; reply_markup = InlineKeyboardMarkup(keyboard)
    summary_msg_parts = [f"{e('AlazNet ID')} {e(str(alaz_id_display))} {e('için sorgu tamamlandı.')}" + (f" {e('Ancak bir sorun oluştu:')} {e(alaz_net_data['_error_'])}" if not alaz_net_data or "_error_" in alaz_net_data else "")]
    sol_data = context.user_data.get('superonline_data', {}); sol_data_field = sol_data.get("data")
    if sol_data.get("status") == "input_error": summary_msg_parts.append(f"{e('Süperonline sorgusu atlandı')}: {e(sol_data.get('error', 'Giriş verisi eksik'))}")
    elif "error" in sol_data: summary_msg_parts.append(f"{e('Süperonline sorgusunda hata')}: {e(sol_data.get('error', 'Bilinmeyen bir hata oluştu'))}")
    elif isinstance(sol_data_field, dict) and "Message" in sol_data_field: summary_msg_parts.append(f"{e('Süperonline API Yanıtı')}: {e(sol_data_field.get('Message', 'Detay yok'))}")
    elif check_superonline(sol_data): summary_msg_parts.append(e("Süperonline Fiber görünüyor!"))
    elif isinstance(sol_data_field, list) and not sol_data_field: summary_msg_parts.append(e("Süperonline altyapısı bulunamadı veya port yok."))
    else: summary_msg_parts.append(e("Süperonline sonucu PDF ve JSON'da detaylıdır."))
    final_text = "\n".join(summary_msg_parts) + "\n\n" + e("Lütfen sonuçları indirin veya yeni bir işlem seçin:")
    chat_id = (update_or_query.effective_chat.id if isinstance(update_or_query, Update) and update_or_query.effective_chat else (update_or_query.message.chat_id if isinstance(update_or_query, CallbackQuery) and update_or_query.message else context.user_data.get('last_chat_id')))
    if not chat_id: logger.error("send_summary_and_buttons: Chat ID yok."); return
    context.user_data['last_chat_id'] = chat_id
    try: await context.bot.send_message(chat_id=chat_id, text=final_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as ex: logger.error(f"send_summary_and_buttons HATA: {ex}"); raw_text_fallback = final_text; [raw_text_fallback := raw_text_fallback.replace(f"\\{c}", c) for c in r'_*[]()~`>#+-=|{}.!']; await context.bot.send_message(chat_id=chat_id, text=raw_text_fallback, reply_markup=reply_markup)

async def show_results_actions_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer(); action = query.data
    api_data_alaz = context.user_data.get('api_data'); alaz_id_for_display = context.user_data.get('alaznet_queried_id', "bilinmiyor")
    if not query.message: logger.error("show_results_actions_callback: query.message None!"); return ConversationHandler.END
    chat_id = query.message.chat_id
    if api_data_alaz is None and action != "new_query": await query.message.reply_text(escape_markdown_v2("Sonuç verisi yok."), parse_mode=ParseMode.MARKDOWN_V2); return SHOW_RESULTS_AND_ACTIONS
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    if action == "download_pdf_detay":
        if api_data_alaz is None: await query.message.reply_text(escape_markdown_v2("PDF için AlazNet verisi yok."), parse_mode=ParseMode.MARKDOWN_V2); return SHOW_RESULTS_AND_ACTIONS
        await query.message.reply_chat_action(action=ChatAction.UPLOAD_DOCUMENT); await query.message.reply_text(escape_markdown_v2("Detaylı PDF hazırlanıyor..."), parse_mode=ParseMode.MARKDOWN_V2)
        try: pdf_buffer = await async_generate_pdf(api_data_alaz, alaz_id_for_display, context, detay_mode=True); await context.bot.send_document(chat_id, document=InputFile(pdf_buffer, filename=f"altyapi_ID_{alaz_id_for_display}_detayli.pdf"), caption=escape_markdown_v2(f"AlazNet ID {alaz_id_for_display} detaylı altyapı raporu."), parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e: logger.error(f"PDF oluşturma/gönderme hatası: {e}", exc_info=True); await query.message.reply_text(escape_markdown_v2(f"PDF hatası: {e}"), parse_mode=ParseMode.MARKDOWN_V2)
    elif action == "download_json":
        await query.message.reply_chat_action(action=ChatAction.UPLOAD_DOCUMENT); await query.message.reply_text(escape_markdown_v2("Ham JSON hazırlanıyor..."), parse_mode=ParseMode.MARKDOWN_V2)
        json_payload = {"sorgulama_zamani_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "sorgulanan_alaznet_id": alaz_id_for_display, "sorgulanan_sol_bbk": context.user_data.get('sol_queried_bbk'), "sorgulanan_sol_bina_kodu_apiye_gonderilen": context.user_data.get('sol_queried_bina_kodu_for_api'), "turk_telekom_alaznet_yaniti": api_data_alaz if api_data_alaz is not None else {"_error_": "AlazNet verisi yok"}, "superonline_dsmart_yaniti": context.user_data.get('superonline_data', {"info": "Süperonline verisi yok"})}
        try: json_str = json.dumps(json_payload, indent=2, ensure_ascii=False); json_bytes = io.BytesIO(json_str.encode('utf-8')); await context.bot.send_document(chat_id, document=InputFile(json_bytes, filename=f"altyapi_ID_{alaz_id_for_display}_tum_veriler.json"), caption=escape_markdown_v2(f"AlazNet ID {alaz_id_for_display} tüm ham altyapı verileri."), parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e: logger.error(f"JSON oluşturma/gönderme hatası: {e}", exc_info=True); await query.message.reply_text(escape_markdown_v2(f"JSON hatası: {e}"), parse_mode=ParseMode.MARKDOWN_V2)
    elif action == "new_query": return await start_new_query(update, context, edit_message=query.message, force_new_message=True)
    return SHOW_RESULTS_AND_ACTIONS

async def start_new_query(update: Update, context: ContextTypes.DEFAULT_TYPE, edit_message: Message | None = None, force_new_message: bool = False) -> int:
    context.user_data.clear(); keyboard = [[InlineKeyboardButton("BBK ile Sorgula", callback_data="query_bbk")], [InlineKeyboardButton("Adres ile Sorgula", callback_data="query_address")]]; reply_markup = InlineKeyboardMarkup(keyboard)
    text_to_send = escape_markdown_v2("Yeni bir altyapı sorgusu için lütfen sorgu türünü seçin:")
    chat_id = (update.effective_chat.id if hasattr(update, 'effective_chat') and update.effective_chat else (edit_message.chat.id if edit_message and edit_message.chat else (update.callback_query.message.chat.id if hasattr(update, 'callback_query') and update.callback_query and update.callback_query.message else None)))
    if not chat_id: logger.error("start_new_query: Chat ID yok."); return CHOOSE_METHOD
    context.user_data['last_chat_id'] = chat_id
    send_as_new = force_new_message or not (edit_message and hasattr(update, 'callback_query') and update.callback_query and update.callback_query.data == "adres_ana_menu")
    if not send_as_new and edit_message:
        try: await edit_message.edit_text(text_to_send, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e: logger.warning(f"start_new_query: Mesaj düzenlenemedi ({e}), yeni gönderiliyor."); await context.bot.send_message(chat_id=chat_id, text=text_to_send, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2)
    else: await context.bot.send_message(chat_id=chat_id, text=text_to_send, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2)
    return CHOOSE_METHOD

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info(f"Kullanıcı {update.effective_user.name if update.effective_user else 'Bilinmiyor'} işlemi iptal etti.")
    await update.message.reply_text(escape_markdown_v2("İşlem iptal edildi. /start ile yeniden başlayın."), parse_mode=ParseMode.MARKDOWN_V2)
    context.user_data.clear(); return ConversationHandler.END

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            CHOOSE_METHOD: [CallbackQueryHandler(choose_method_callback, pattern='^(query_bbk|query_address)$')], ASK_BBK: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_bbk)], ASK_CITY_PLATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_city_plate)],
            CHOOSE_DISTRICT: [CallbackQueryHandler(adres_callback_handler)], CHOOSE_NEIGHBORHOOD: [CallbackQueryHandler(adres_callback_handler)],
            CHOOSE_STREET: [CallbackQueryHandler(adres_callback_handler)], CHOOSE_BUILDING: [CallbackQueryHandler(adres_callback_handler)],
            CHOOSE_APARTMENT: [CallbackQueryHandler(adres_callback_handler)], SHOW_RESULTS_AND_ACTIONS: [CallbackQueryHandler(show_results_actions_callback, pattern='^(download_pdf_detay|download_json|new_query)$')]
        },
        fallbacks=[CommandHandler('cancel', cancel), CommandHandler('start', start)], allow_reentry=True
    )
    app.add_handler(conv_handler); logger.info("Bot başlatılıyor..."); app.run_polling(allowed_updates=Update.ALL_TYPES); logger.info("Bot durduruldu.")

if __name__ == "__main__":
    if not logging.getLogger().handlers: logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
    main()
