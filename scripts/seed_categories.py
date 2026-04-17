"""Seed the category taxonomy with hierarchical parent/child relationships.

Uses deterministic UUIDs (uuid5) for repeatable seeding and ON CONFLICT DO UPDATE
for idempotency. Safe to run multiple times.

Usage:
    python -m scripts.seed_categories
"""

import asyncio
import uuid

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.models.category import Category

# Category taxonomy: top-level -> subcategories
# Each entry: slug -> (name, description, [children])
TAXONOMY: dict[str, tuple[str, str, dict[str, tuple[str, str]]]] = {
    "technology": (
        "Technology",
        "Computer science, AI, software engineering, and digital systems",
        {
            "artificial-intelligence": ("Artificial Intelligence", "Machine learning, deep learning, AGI research"),
            "software-engineering": ("Software Engineering", "Programming, architecture, and development practices"),
            "cybersecurity": ("Cybersecurity", "Information security, cryptography, and privacy"),
        },
    ),
    "science": (
        "Science",
        "Natural sciences, physics, biology, chemistry, and mathematics",
        {
            "neuroscience": ("Neuroscience", "Brain science, cognition, and neural systems"),
            "physics": ("Physics", "Fundamental forces, cosmology, and quantum mechanics"),
            "biology": ("Biology", "Life sciences, genetics, and evolutionary biology"),
        },
    ),
    "philosophy": (
        "Philosophy",
        "Ethics, epistemology, metaphysics, and philosophy of mind",
        {
            "ethics": ("Ethics", "Moral philosophy, applied ethics, and bioethics"),
            "philosophy-of-mind": ("Philosophy of Mind", "Consciousness, free will, and cognition"),
            "epistemology": ("Epistemology", "Theory of knowledge, truth, and belief"),
        },
    ),
    "economics": (
        "Economics",
        "Economic theory, markets, policy, and financial systems",
        {
            "macroeconomics": ("Macroeconomics", "National economies, monetary policy, and fiscal systems"),
            "behavioral-economics": ("Behavioral Economics", "Psychology of decision-making and market behavior"),
        },
    ),
}


def _category_id(slug: str) -> uuid.UUID:
    """Generate a deterministic UUID for a category based on its slug."""
    return uuid.uuid5(uuid.NAMESPACE_DNS, f"thinktank.category.{slug}")


async def seed_categories(session: AsyncSession) -> int:
    """Seed the category taxonomy into the database.

    Uses ON CONFLICT DO UPDATE for idempotent upserts.
    Returns the number of categories seeded.
    """
    count = 0

    for slug, (name, description, children) in TAXONOMY.items():
        parent_id = _category_id(slug)

        # Insert top-level category
        stmt = (
            insert(Category)
            .values(
                id=parent_id,
                slug=slug,
                name=name,
                description=description,
                parent_id=None,
            )
            .on_conflict_do_update(
                index_elements=["slug"],
                set_={"name": name, "description": description, "parent_id": None},
            )
        )
        await session.execute(stmt)
        count += 1

        # Insert subcategories
        for child_slug, (child_name, child_desc) in children.items():
            child_id = _category_id(child_slug)
            stmt = (
                insert(Category)
                .values(
                    id=child_id,
                    slug=child_slug,
                    name=child_name,
                    description=child_desc,
                    parent_id=parent_id,
                )
                .on_conflict_do_update(
                    index_elements=["slug"],
                    set_={"name": child_name, "description": child_desc, "parent_id": parent_id},
                )
            )
            await session.execute(stmt)
            count += 1

    return count


if __name__ == "__main__":

    async def _main() -> None:
        from thinktank.database import async_session_factory

        async with async_session_factory() as session:
            count = await seed_categories(session)
            await session.commit()
            print(f"Seeded {count} categories")

    asyncio.run(_main())
