# Import models so they register on Base.metadata when main imports app.models.
from app.models.user import User, FarmerProfile, ConsumerProfile  # noqa: F401
from app.models.product import Product  # noqa: F401
from app.models.order import Order, OrderItem, CartItem  # noqa: F401
from app.models.payment import Payment, PaymentMethod  # noqa: F401
from app.models.payout import FarmerBankAccount, FarmerPayout  # noqa: F401
