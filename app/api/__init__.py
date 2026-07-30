from app.api import auto_director as auto_director
from app.api import director_plan as director_plan

# Keep the public route under /api/auto-director without changing app.main imports.
auto_director.router.include_router(director_plan.router)
