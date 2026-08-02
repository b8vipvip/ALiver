from app.api import auto_director as auto_director
from app.api import director_plan as director_plan

# Keep the director-plan feature attached to the router imported by app.main.
auto_director.router.include_router(director_plan.router)
