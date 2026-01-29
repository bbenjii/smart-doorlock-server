import firebase_admin
from firebase_admin import firestore
from firebase_admin import credentials
cred = credentials.Certificate("smart-doorlock-project-firebase-adminsdk-fbsvc-035584259e.json")
app = firebase_admin.initialize_app(cred)

# Application Default credentials are automatically created.
db = firestore.client()
