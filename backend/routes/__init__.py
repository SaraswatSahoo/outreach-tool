"""Flask blueprints, one per feature area. All routes live under /api."""

from routes.campaigns import bp as campaigns_bp
from routes.crm import bp as crm_bp
from routes.followups import bp as followups_bp
from routes.imports import bp as imports_bp
from routes.inbox import bp as inbox_bp
from routes.profile import bp as profile_bp
from routes.settings import bp as settings_bp

ALL_BLUEPRINTS = (profile_bp, settings_bp, campaigns_bp, followups_bp, crm_bp, inbox_bp, imports_bp)
