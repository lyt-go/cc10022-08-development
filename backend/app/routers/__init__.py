"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。

校验类动作路由必须优先注册：资源路由含 /api/{module}/{entry_id}，
后注册会被动态路径抢走 /api/data_import_rule/{id}/field-mapping-validation
这类固定后缀路径。
"""
from __future__ import annotations

from app.routers import resource, validation

ROUTERS = [validation, resource]
