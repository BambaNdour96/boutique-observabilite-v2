import random
import uuid
from locust import HttpUser, task, between

PRODUCT_IDS = ["1","2","3","4","5","6","7","8"]

class ShopUser(HttpUser):
    wait_time = between(1, 3)
    
    def on_start(self):
        self.user_id = str(uuid.uuid4())[:8]
        self.session_products = []

    @task(5)
    def browse_home(self):
        self.client.get("/", name="home")

    @task(4)
    def browse_product(self):
        pid = random.choice(PRODUCT_IDS)
        with self.client.get(f"/product/{pid}", name="/product/[id]", catch_response=True) as r:
            if r.status_code == 504:
                r.failure(f"Product {pid} timeout — catalog failure")
            elif r.status_code != 200:
                r.failure(f"Product {pid} error {r.status_code}")

    @task(3)
    def add_to_cart(self):
        pid = random.choice(PRODUCT_IDS)
        with self.client.post("/cart/add",
            data={"product_id": pid, "quantity": 1},
            name="/cart/add",
            catch_response=True,
            allow_redirects=True) as r:
            if r.status_code == 503:
                r.failure("Cart service unavailable — cart failure")

    @task(2)
    def view_cart(self):
        self.client.get("/cart", name="cart")

    @task(1)
    def full_checkout(self):
        pid = random.choice(PRODUCT_IDS)
        self.client.post("/cart/add",
            data={"product_id": pid, "quantity": 1},
            name="/cart/add",
            allow_redirects=True)
        with self.client.post("/checkout",
            data={},
            name="/checkout",
            catch_response=True,
            allow_redirects=True) as r:
            if r.status_code == 402:
                r.failure("Payment rejected — payment failure injected")
            elif r.status_code == 503:
                r.failure("Order service unavailable")
