"""Category taxonomy management router.

Provides category tree viewing, creation, updating, and deletion
with hierarchical display using Jinja2 recursive macros.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from thinktank.admin.dependencies import get_session, get_templates

router = APIRouter(prefix="/admin/categories", tags=["categories"])
templates = get_templates()


async def _get_category_tree(session: AsyncSession) -> list:
    """Load all root categories with children eagerly loaded."""
    from thinktank.models.category import Category

    result = await session.execute(
        select(Category)
        .where(Category.parent_id.is_(None))
        .options(selectinload(Category.children, recursion_depth=5))
        .order_by(Category.name)
    )
    return list(result.scalars().unique().all())


@router.get("/")
async def categories_page(request: Request):
    """Render the full category management page."""
    return templates.TemplateResponse(request, "categories.html")


@router.get("/partials/tree")
async def category_tree_partial(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """HTML fragment: hierarchical category tree."""
    roots = await _get_category_tree(session)
    return templates.TemplateResponse(
        request,
        "partials/category_tree.html",
        {"roots": roots},
    )


@router.post("/create")
async def create_category(
    request: Request,
    session: AsyncSession = Depends(get_session),
    name: str = Form(...),
    slug: str = Form(...),
    description: str = Form(""),
    parent_id: str = Form(""),
):
    """Create a new category via form submission."""
    from thinktank.models.category import Category

    cat = Category(
        name=name,
        slug=slug,
        description=description or f"Category: {name}",
        parent_id=UUID(parent_id) if parent_id else None,
    )
    session.add(cat)
    await session.commit()

    # Re-render tree
    roots = await _get_category_tree(session)
    return templates.TemplateResponse(
        request,
        "partials/category_tree.html",
        {"roots": roots},
    )


@router.post("/update/{category_id}")
async def update_category(
    category_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    name: str = Form(...),
    description: str = Form(""),
):
    """Update a category's name and description."""
    from thinktank.models.category import Category

    cat = await session.get(Category, category_id)
    if not cat:
        roots = await _get_category_tree(session)
        return templates.TemplateResponse(
            request,
            "partials/category_tree.html",
            {"roots": roots, "error": "Category not found"},
            status_code=404,
        )

    cat.name = name
    if description:
        cat.description = description
    await session.commit()

    roots = await _get_category_tree(session)
    return templates.TemplateResponse(
        request,
        "partials/category_tree.html",
        {"roots": roots},
    )


@router.post("/delete/{category_id}")
async def delete_category(
    category_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Delete a category if it has no children and no thinker associations."""
    from thinktank.models.category import Category

    cat = await session.get(Category, category_id)
    if not cat:
        roots = await _get_category_tree(session)
        return templates.TemplateResponse(
            request,
            "partials/category_tree.html",
            {"roots": roots, "error": "Category not found"},
            status_code=404,
        )

    # Check for children
    children_result = await session.execute(
        text("SELECT COUNT(*) FROM categories WHERE parent_id = :id"),
        {"id": category_id},
    )
    if (children_result.scalar() or 0) > 0:
        roots = await _get_category_tree(session)
        return templates.TemplateResponse(
            request,
            "partials/category_tree.html",
            {"roots": roots, "error": "Cannot delete category with children"},
            status_code=400,
        )

    # Check for thinker associations
    assoc_result = await session.execute(
        text("SELECT COUNT(*) FROM thinker_categories WHERE category_id = :id"),
        {"id": category_id},
    )
    if (assoc_result.scalar() or 0) > 0:
        roots = await _get_category_tree(session)
        return templates.TemplateResponse(
            request,
            "partials/category_tree.html",
            {"roots": roots, "error": "Cannot delete category with thinker associations"},
            status_code=400,
        )

    await session.delete(cat)
    await session.commit()

    roots = await _get_category_tree(session)
    return templates.TemplateResponse(
        request,
        "partials/category_tree.html",
        {"roots": roots},
    )
