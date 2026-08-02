from app.api import auto_director as auto_director
from app.api import dashboard as dashboard
from app.api import director_plan as director_plan
from app.api import health as health
from app.api import livetalking_cloud as livetalking_cloud

# Keep optional feature routes attached to routers that app.main already imports.
auto_director.router.include_router(director_plan.router)
dashboard.router.include_router(livetalking_cloud.admin_router)
health.router.include_router(livetalking_cloud.public_router)
