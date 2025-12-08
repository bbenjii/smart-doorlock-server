import requests


base_url = "https://bae972a4367e.ngrok-free.app/"

def lock():
    res = requests.post(base_url + "lock")

def unlock():
    res = requests.post(base_url + "unlock")

def get_status():
    res = requests.get(base_url + "status")
    pass
    
print(get_status())
# lock()
# unlock()