import os
import uuid
import shutil
from datetime import datetime, timedelta
from typing import List
from fastapi import FastAPI, Depends, Form, UploadFile, File, Request, HTTPException, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, Text, ForeignKey, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship, Session
from translations import DASHBOARD_LANGUAGES, STOREFRONT_LANGUAGES


DATABASE_URL = "sqlite:///./test_store.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ─── RELATIONAL DATA MODELS ───
class Seller(Base):
    __tablename__ = 'sellers'
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    shop_name = Column(String)
    phone = Column(String)
    logo_url = Column(String, nullable=True)
    telegram_link = Column(String, nullable=True)
    
    pin_code = Column(String, nullable=False)
    failed_attempts = Column(Integer, default=0)
    lockout_until = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True)
    plan_expiry_date = Column(DateTime, nullable=False)
    payment_status = Column(String, default="Trial")

    products = relationship("Product", back_populates="owner", cascade="all, delete-orphan")
    categories = relationship("Category", back_populates="owner", cascade="all, delete-orphan")

class Category(Base):
    __tablename__ = 'categories'
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    seller_id = Column(Integer, ForeignKey('sellers.id'))
    owner = relationship("Seller", back_populates="categories")
    products = relationship("Product", back_populates="category_rel")

class Product(Base):
    __tablename__ = 'products'
    id = Column(Integer, primary_key=True, index=True)
    seller_id = Column(Integer, ForeignKey('sellers.id'))
    category_id = Column(Integer, ForeignKey('categories.id'), nullable=True)
    name = Column(String)
    price = Column(Float)
    description = Column(Text, nullable=True)
    image_url = Column(String, nullable=True)
    is_available = Column(Boolean, default=True)
    
    owner = relationship("Seller", back_populates="products")
    category_rel = relationship("Category", back_populates="products")

Base.metadata.create_all(bind=engine)

app = FastAPI()
os.makedirs("static/uploads", exist_ok=True)
try: app.mount("/static", StaticFiles(directory="static"), name="static")
except RuntimeError: pass

templates = Jinja2Templates(directory="templates")
ADMIN_SECRET_TOKEN = "khmer_saas_999"

def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

# ─── DASHBOARD MULTI-LINGUAL TRANSLATION MAP ───


# ─── SECURITY COOKIE BADGE UTILITIES ───
def verify_session_badge(request: Request, seller_username: str) -> bool:
    badge = request.cookies.get(f"session_{seller_username.lower()}")
    return badge == "session_approved_lock"

# ─── ROUTE CONTROLLERS ───
@app.get("/super-admin-panel", response_class=HTMLResponse)
async def view_super_admin_panel(request: Request, db: Session = Depends(get_db)):
    t = request.query_params.get("token", "").strip()
    if t != ADMIN_SECRET_TOKEN: 
        return HTMLResponse("🔒 Restricted")
    return templates.TemplateResponse(request, "super_admin.html", {
        "request": request, 
        "sellers": db.query(Seller).all(), 
        "token": t
    })
@app.get("/super-admin/activate-30-days/{seller_id}")
async def admin_extend_paid_month(seller_id: int, token: str = None, db: Session = Depends(get_db)):
    if token != ADMIN_SECRET_TOKEN: 
        raise HTTPException(status_code=401)
    s = db.query(Seller).filter(Seller.id == seller_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Tenant missing")
        
    s.plan_expiry_date = max(s.plan_expiry_date, datetime.now()) + timedelta(days=30)
    s.is_active = True
    s.payment_status = "Paid"
    db.commit()
    return RedirectResponse(url=f"/super-admin-panel?token={token}", status_code=303)
# ─── PASTE THIS RIGHT BELOW YOUR admin_extend_paid_month FUNCTION ───

@app.get("/super-admin/suspend/{seller_id}")
async def admin_suspend_tenant(seller_id: int, token: str = None, db: Session = Depends(get_db)):
    if token != ADMIN_SECRET_TOKEN: 
        raise HTTPException(status_code=401)
        
    s = db.query(Seller).filter(Seller.id == seller_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Tenant missing")
        
    # Freezes the storefront and sets billing statuses
    s.is_active = False
    s.payment_status = "Suspended"
    db.commit()
    
    return RedirectResponse(url=f"/super-admin-panel?token={token}", status_code=303)

@app.get("/", response_class=HTMLResponse)
async def view_registration_page(request: Request):
    return templates.TemplateResponse(request, "register.html", {"request": request})

@app.post("/register-shop")
async def register_new_shop(username: str = Form(...), shop_name: str = Form(...), phone: str = Form(...), pin_code: str = Form(...), db: Session = Depends(get_db)):
    clean_username = username.strip().lower().replace(" ", "_")
    if db.query(Seller).filter(Seller.username == clean_username).first():
        raise HTTPException(status_code=400, detail="Slug taken!")
    if len(pin_code) != 4 or not pin_code.isdigit():
         raise HTTPException(status_code=400, detail="PIN must be 4 digits.")
        
    new_seller = Seller(
        username=clean_username, shop_name=shop_name, phone=phone, 
        pin_code=pin_code, is_active=True, payment_status="Trial",
        plan_expiry_date=datetime.now() + timedelta(days=14)
    )
    db.add(new_seller)
    db.commit()
    db.refresh(new_seller)
    
    default_cats = ["ទូទៅ", "សម្លៀកបំពាក់", "ស្បែកជើង", "កាបូប", "គ្រឿងសម្អាង"]
    for cat_name in default_cats:
        db.add(Category(name=cat_name, seller_id=new_seller.id))
    db.commit()
    
    return RedirectResponse(url=f"/dashboard?shop={clean_username}", status_code=303)

@app.api_route("/dashboard", methods=["GET", "POST"], response_class=HTMLResponse)
async def view_dashboard(request: Request, response: Response, shop: str = "sky_fashion", pin_attempt: str = Form(None), db: Session = Depends(get_db)):
    is_authenticated = False
    error_msg = None
    is_locked_out = False
    minutes_left = 0
    days_left = 0
    products = []
    seller_categories = []

    shop_slug = shop.strip().lower()
    seller = db.query(Seller).filter(Seller.username == shop_slug).first()
    
    if seller is None:
        if shop_slug == "sky_fashion":
            print("🔧 Re-seeding clean sky_fashion profile...")
            seller = Seller(
                username="sky_fashion", shop_name="Sky Fashion KH", phone="012 345 678", 
                pin_code="1234", is_active=True, plan_expiry_date=datetime.now() + timedelta(days=365)
            )
            db.add(seller)
            db.commit()
            db.refresh(seller)
            
            default_cats = ["ទូទៅ", "សម្លៀកបំពាក់", "ស្បែកជើង", "កាបូប", "គ្រឿងសម្អាង"]
            for cat_name in default_cats:
                db.add(Category(name=cat_name, seller_id=seller.id))
            db.commit()
        else:
            return RedirectResponse(url="/", status_code=303)
        
    if seller.plan_expiry_date and datetime.now() > seller.plan_expiry_date:
        seller.is_active = False
        seller.payment_status = "Overdue"
        db.commit()

    is_authenticated = verify_session_badge(request, seller.username)

    if seller.lockout_until:
        if datetime.now() < seller.lockout_until:
            is_locked_out = True
            minutes_left = int((seller.lockout_until - datetime.now()).total_seconds() / 60) + 1
        else:
            seller.lockout_until = None
            seller.failed_attempts = 0
            db.commit()

    if pin_attempt and not is_locked_out:
        if pin_attempt == seller.pin_code:
            is_authenticated = True
            seller.failed_attempts = 0
            db.commit()
        else:
            seller.failed_attempts += 1
            remaining_tries = 4 - seller.failed_attempts
            if seller.failed_attempts >= 4:
                seller.lockout_until = datetime.now() + timedelta(minutes=30)
                is_locked_out = True
                minutes_left = 30
                error_msg = "🛑 Too many wrong entries. Keypad frozen for 30 minutes."
            else:
                error_msg = f"❌ Invalid PIN. {remaining_tries} attempts remaining."
            db.commit()

    products = db.query(Product).filter(Product.seller_id == seller.id).all()
    if seller.plan_expiry_date:
        days_left = max(0, (seller.plan_expiry_date - datetime.now()).days)

    seller_categories = db.query(Category).filter(Category.seller_id == seller.id).all()

    chosen_lang = request.cookies.get("dashboard_lang", "km")
    lang_dictionary = DASHBOARD_LANGUAGES.get(chosen_lang, DASHBOARD_LANGUAGES["km"])

    context = {
        "request": request, "seller": seller, "products": products, "days_left": days_left, 
        "is_authenticated": is_authenticated, "error_msg": error_msg, "is_locked_out": is_locked_out, 
        "minutes_left": minutes_left, "categories": seller_categories,
        "lang": chosen_lang, "txt": lang_dictionary
    }

    if is_authenticated and pin_attempt:
        res = templates.TemplateResponse(request, "dashboard.html", context)
        res.set_cookie(key=f"session_{seller.username}", value="session_approved_lock", httponly=True)
        return res

    return templates.TemplateResponse(request, "dashboard.html", context)

@app.get("/dashboard/set-lang")
async def dashboard_switch_language(shop: str, lang: str):
    target_lang = "km" if lang.lower() == "km" else "en"
    res = RedirectResponse(url=f"/dashboard?shop={shop.lower()}", status_code=303)
    res.set_cookie(key="dashboard_lang", value=target_lang, max_age=31536000)
    return res

# ─── NEW: BUYER STOREFRONT LANGUAGE SWITCHER ROUTE ───
@app.get("/{username}/set-lang")
async def storefront_switch_language(username: str, lang: str):
    target_lang = "km" if lang.lower() == "km" else "en"
    res = RedirectResponse(url=f"/{username.lower()}", status_code=303)
    res.set_cookie(key="storefront_lang", value=target_lang, max_age=31536000)
    return res

@app.get("/dashboard/logout")
async def process_session_logout(shop: str):
    res = RedirectResponse(url=f"/dashboard?shop={shop.lower()}", status_code=303)
    res.delete_cookie(key=f"session_{shop.lower()}")
    return res

@app.post("/dashboard/update-logo")
async def update_logo(seller_id: int = Form(...), logo_file: UploadFile = File(...), db: Session = Depends(get_db)):
    seller = db.query(Seller).filter(Seller.id == seller_id).first()
    if logo_file.filename:
        unique_id = f"LOGO-{uuid.uuid4()}{os.path.splitext(logo_file.filename)[1]}"
        logo_url = f"/static/uploads/{unique_id}"
        with open(logo_url.strip("/"), "wb") as buffer: shutil.copyfileobj(logo_file.file, buffer)
        seller.logo_url = logo_url
        db.commit()
    return RedirectResponse(url=f"/dashboard?shop={seller.username}", status_code=303)

@app.post("/dashboard/save-telegram")

async def save_merchant_telegram_link(seller_id: int = Form(...), tg_link: str = Form(...), db: Session = Depends(get_db)):
    # 1. Look up by ID first
    seller = db.query(Seller).filter(Seller.id == seller_id).first()
    
    # Safety Guard: If ID mismatches due to a database reset, fallback to the default profile
    if not seller:
        seller = db.query(Seller).filter(Seller.username == "sky_fashion").first()
        
    if not seller:
        raise HTTPException(status_code=404, detail="Seller profile missing entirely")
        
    # 2. Clean and format the input if the link hasn't been locked yet
    if not seller.telegram_link:
        # Remove all spaces and convert to lowercase for link consistency
        raw_input = tg_link.strip().replace(" ", "").lower()
        
        # If they pasted a full link, extract just the username part first to clean it uniformly
        if "t.me/" in raw_input:
            username = raw_input.split("t.me/")[-1]
        elif "telegram.me/" in raw_input:
            username = raw_input.split("telegram.me/")[-1]
        else:
            username = raw_input

        # CRITICAL FIX: Strip away the '@' symbol if it exists at the start of the username
        clean_username = username.lstrip("@")
        
        # Build the final official clean link architecture
        seller.telegram_link = f"https://t.me/{clean_username}"
        db.commit()
        
    return RedirectResponse(url=f"/dashboard?shop={seller.username}", status_code=303)


@app.post("/dashboard/add-category")
async def add_custom_category(seller_id: int = Form(...), cat_name: str = Form(...), db: Session = Depends(get_db)):
    seller = db.query(Seller).filter(Seller.id == seller_id).first()
    clean_name = cat_name.strip()
    if clean_name:
        exists = db.query(Category).filter(Category.seller_id == seller_id, Category.name == clean_name).first()
        if not exists:
            db.add(Category(name=clean_name, seller_id=seller_id))
            db.commit()
    return RedirectResponse(url=f"/dashboard?shop={seller.username}", status_code=303)

@app.post("/dashboard/add-product")
async def add_product(seller_id: int = Form(...), category_id: int = Form(...), name: str = Form(...), price: float = Form(...), description: str = Form(None), files: List[UploadFile] = File(None), db: Session = Depends(get_db)):
    saved_urls = []
    if files:
        # STORAGE GUARD: Keep only the first 4 files submitted by the user
        secured_files = files[:4]
        
        for file in secured_files:
            if file.filename:
                uid = f"{uuid.uuid4()}{os.path.splitext(file.filename)[1]}"
                path = f"static/uploads/{uid}"
                with open(path, "wb") as buf: shutil.copyfileobj(file.file, buf)
                saved_urls.append(f"/{path}")
                
    db.add(Product(seller_id=seller_id, category_id=category_id, name=name, price=price, description=description, image_url=",".join(saved_urls) if saved_urls else None))
    db.commit()
    return RedirectResponse(url=f"/dashboard?shop={db.query(Seller).filter(Seller.id == seller_id).first().username}", status_code=303)
@app.post("/dashboard/edit-product")
async def edit_product(product_id: int = Form(...), category_id: int = Form(...), name: str = Form(...), price: float = Form(...), description: str = Form(None), files: List[UploadFile] = File(None), db: Session = Depends(get_db)):
    product = db.query(Product).filter(Product.id == product_id).first()
    product.name = name
    product.price = price
    product.category_id = category_id
    product.description = description
    
    if files and any(f.filename for f in files):
        saved_urls = []
        # STORAGE GUARD: Keep only the first 4 files submitted by the user
        secured_files = files[:4]
        
        for file in secured_files:
            if file.filename:
                uid = f"{uuid.uuid4()}{os.path.splitext(file.filename)[1]}"
                path = f"static/uploads/{uid}"
                with open(path, "wb") as buf: shutil.copyfileobj(file.file, buf)
                saved_urls.append(f"/{path}")
        product.image_url = ",".join(saved_urls)
        
    db.commit()
    return RedirectResponse(url=f"/dashboard?shop={db.query(Seller).filter(Seller.id == product.seller_id).first().username}", status_code=303)
@app.get("/dashboard/delete-product/{product_id}")
async def delete_product(product_id: int, db: Session = Depends(get_db)):
    product = db.query(Product).filter(Product.id == product_id).first()
    seller = db.query(Seller).filter(Seller.id == product.seller_id).first()
    db.delete(product)
    db.commit()
    return RedirectResponse(url=f"/dashboard?shop={seller.username}", status_code=303)

@app.get("/dashboard/delete-category/{cat_id}")
async def delete_custom_category(cat_id: int, db: Session = Depends(get_db)):
    category = db.query(Category).filter(Category.id == cat_id).first()
    if category:
        seller = db.query(Seller).filter(Seller.id == category.seller_id).first()
        db.query(Product).filter(Product.category_id == cat_id).update({Product.category_id: None})
        db.delete(category)
        db.commit()
        return RedirectResponse(url=f"/dashboard?shop={seller.username}", status_code=303)
    return RedirectResponse(url="/", status_code=303)

@app.get("/{username}", response_class=HTMLResponse)
async def render_customer_storefront(username: str, request: Request, db: Session = Depends(get_db)):
    s = db.query(Seller).filter(Seller.username == username.lower()).first()
    if not s or not s.is_active: 
        raise HTTPException(status_code=404, detail="Store missing or suspended")
        
    products = db.query(Product).filter(Product.seller_id == s.id).all()
    active_cat_ids = set([p.category_id for p in products if p.category_id is not None])
    buyer_categories = db.query(Category).filter(Category.id.in_(active_cat_ids)).all() if active_cat_ids else []
    
    serialized = []
    for p in products:
        serialized.append({
            "id": p.id, "name": p.name, "price": p.price, "category_id": p.category_id,
            "riel_price": "{:,.0f}".format(p.price * 4100), "description": p.description if p.description else "", 
            "images": p.image_url.split(",") if p.image_url else []
        })
        
    # Read or default the storefront language preference cookie
    buyer_lang = request.cookies.get("storefront_lang", "km")
    store_dict = STOREFRONT_LANGUAGES.get(buyer_lang, STOREFRONT_LANGUAGES["km"])
        
    return templates.TemplateResponse(request, "storefront.html", {
        "request": request, "seller": s, "products": products, "products_data": serialized, 
        "categories": buyer_categories, "lang": buyer_lang, "txt": store_dict
    })
