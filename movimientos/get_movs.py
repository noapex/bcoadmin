from django.conf import settings
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from time import sleep
import re
import time

def notify(message):
	key='umqxyax9sufqsepc635ngrowbqyjhb'
	token='af3u6frnmxuyu7fgnxiq8tn47mpbxo'
	import requests
	r = requests.post("https://api.pushover.net/1/messages.json", data = {
	"token": token,
	"user": key,
	"sound": 'bicycle',
	"message": message })
	print(r.text)


def logout(driver):
	try:
		btn_out = find_with_timeout(driver, '//*[@id="topbar"]/div[1]/div/div[3]/a[6]', 10)
		btn_out.click()
		sleep(1)
		driver.save_screenshot('snap_2.png')
		confirm = find_with_timeout(driver, '/html/body/div[2]/md-dialog/topbar-logout-dialog/div/md-dialog-actions/div[2]/obp-boton/button', 10)
		confirm.click()
		driver.close()
		driver.quit()
	except:
		print('no se pudo desloguear')
		driver.close()
		driver.quit()

def find_with_timeout(driver, xpath, timeout):
	timeout = 120
	end_time = time.time() + timeout
	element = None
	n = 0
	while True:
		n += 1
		print(xpath, n)
		try:
			element = driver.find_element("xpath", xpath)
		except:
			pass
		if element:
			return element
		time.sleep(1)
		if time.time() > end_time:
			break
	raise AssertionError


def download_movs(path):
	prefs = {"download.default_directory" : path}
	options = Options()
	options.add_argument("start-maximized"); # open Browser in maximized mode
	options.add_argument("disable-infobars"); # disabling infobars
	options.add_argument("--disable-extensions"); # disabling extensions
	options.add_argument("--disable-gpu"); # applicable to windows os only
	options.add_argument("--disable-dev-shm-usage"); # overcome limited resource problems
	options.add_argument("--no-sandbox"); # Bypass OS security model
	options.headless = True
	options.add_experimental_option("prefs",prefs)
	options.add_argument("--window-size=1920,1200")
	total_actual = 224

	d = webdriver.Chrome(options=options)
	d.get('https://www2.personas.santander.com.ar/obp-webapp/angular/#!/login')

	try:
		doc = find_with_timeout(d, '//*[@id="input_0"]', 10)
		clave = find_with_timeout(d, '//*[@id="input_1"]', 10)
		user = find_with_timeout(d, '//*[@id="input_2"]', 10)
		doc.send_keys(settings.BCO_DOC)
		clave.send_keys(settings.BCO_CLAVE)
		user.send_keys(settings.BCO_USER)
		login = find_with_timeout(d, '//*[@id="form"]/button', 10)
		login.click()
		time.sleep(2)
		try:
			wait_time = find_with_timeout(d, '//*[@id="dialogContent_4"]/div/span/div/div/p', 10)
			print('wait_time', wait_time)
			minutes, seconds = re.findall('([\d]+) minuto ([\d]+) segundos', wait_time.text)[0]
			total_wait_seconds = int(minutes)*60+int(seconds)+10
			print(f'esperando {total_wait_seconds}')
			time.sleep(total_wait_seconds)

		except Exception as e:
			print(e)
			pass

		d.get('https://www2.personas.santander.com.ar/obp-webapp/angular/#!/cuentas-inicio')
		#sleep(2)
		buscar = find_with_timeout(d, '//*[@id="grilla"]/cuentas-inicio-movimientos/div[2]/obp-selector/div/div/div/a', 10)
		buscar.click()
		#sleep(1)
		ultimos_60 = find_with_timeout(d, '//*[@id="grilla"]/cuentas-inicio-movimientos/div[2]/obp-selector/div/ng-transclude/cuentas-buscador/form/div[2]/obp-boton/button', 10)
		ultimos_60.click()
		#sleep(1)
		descargar = find_with_timeout(d, '//*[@id="grilla"]/cuentas-inicio-movimientos/div[2]/div[4]/div[2]/footer/a[2]', 10)
		descargar.click()
		#sleep(2)
		#file_name = getDownLoadedFileName(d, 10)
	except Exception as e:
		print(f'Error: {e}')
		logout(d)
		return False

	logout(d)
