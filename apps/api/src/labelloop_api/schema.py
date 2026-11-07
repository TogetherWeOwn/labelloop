"""PostgreSQL canonical records; Alembic owns their creation and append-only guards."""

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

metadata = MetaData()


def table(name: str, *columns: Column, constraints: tuple = ()) -> Table:
    return Table(
        name,
        metadata,
        Column("id", UUID, primary_key=True, server_default=text("gen_random_uuid()")),
        *columns,
        *constraints,
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )


project = table(
    "project",
    Column("project_key", String, unique=True, nullable=False),
    Column("workspace_id", UUID, nullable=False),
    Column("task", Text, nullable=False),
    Column("scope", Text, nullable=False),
    Column("domain", String, nullable=False),
    Column("access_policy", JSON, nullable=False, server_default="{}"),
)
ontology_release = table(
    "ontology_release",
    Column("ontology_key", String, nullable=False),
    Column("project_id", UUID, ForeignKey("project.id"), nullable=False),
    Column("version", Integer, nullable=False),
    Column("classes", JSON, nullable=False),
    Column("class_map", JSON, nullable=False),
    constraints=(UniqueConstraint("project_id", "version"),),
)
source_asset = table(
    "source_asset",
    Column("asset_key", String, unique=True, nullable=False),
    Column("project_id", UUID, ForeignKey("project.id"), nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("object_key", String, nullable=False),
    Column("width", Integer, nullable=False),
    Column("height", Integer, nullable=False),
    Column("exif_orientation", Integer, nullable=False),
    Column("source_uri", Text, nullable=False),
    constraints=(
        CheckConstraint("width > 0 AND height > 0"),
        CheckConstraint("exif_orientation BETWEEN 1 AND 8"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'"),
        CheckConstraint("object_key = 'sha256/' || substr(sha256,1,2) || '/' || sha256"),
    ),
)
reference_example = table(
    "reference_example",
    Column("asset_id", UUID, ForeignKey("source_asset.id"), nullable=False),
    Column("class_id", String, nullable=False),
    Column("crop", JSON),
    Column("evidence_provenance", Text, nullable=False),
    Column("approved", Boolean, nullable=False),
    Column("validity_scope", Text, nullable=False),
    Column("embedding_model", String),
)
annotation_revision = table(
    "annotation_revision",
    Column("asset_id", UUID, ForeignKey("source_asset.id"), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("parent_revision", Integer),
    Column("actor_kind", String, nullable=False),
    Column("actor_id", String, nullable=False),
    Column("objects", JSON, nullable=False),
    Column("label_state", String, nullable=False, server_default="identified"),
    Column("approval_state", String, nullable=False),
    Column("review_tier", String, nullable=False),
    Column("scope_completeness", String, nullable=False),
    Column("training_eligible", Boolean, nullable=False),
    Column("gold_eligible", Boolean, nullable=False),
    constraints=(
        UniqueConstraint("asset_id", "revision"),
        CheckConstraint("actor_kind IN ('human','model','system')"),
        CheckConstraint("approval_state <> 'human_accepted' OR actor_kind = 'human'"),
        CheckConstraint(
            "review_tier NOT IN ('human_reviewed','adjudicated') OR actor_kind = 'human'"
        ),
        CheckConstraint(
            "(parent_revision IS NULL AND revision = 1) OR parent_revision = revision - 1"
        ),
        CheckConstraint("NOT gold_eligible OR review_tier = 'adjudicated'"),
        CheckConstraint(
            "NOT training_eligible OR actor_kind = 'human' OR "
            "(actor_kind = 'system' AND approval_state = 'auto_accepted' "
            "AND review_tier = 'machine_verified_no_human_review')"
        ),
        CheckConstraint(
            "NOT (training_eligible OR gold_eligible) OR (scope_completeness = "
            "'exhaustive_for_scope' AND approval_state IN ('auto_accepted','human_accepted'))"
        ),
    ),
)
review_event = table(
    "review_event",
    Column("asset_id", UUID, ForeignKey("source_asset.id"), nullable=False),
    Column("actor_kind", String, nullable=False),
    Column("actor_id", String, nullable=False),
    Column("decision", String, nullable=False),
    Column("expected_revision", BigInteger, nullable=False),
    Column("correction", JSON),
    Column("reason", Text, nullable=False),
)
completeness_assessment = table(
    "completeness_assessment",
    Column("asset_id", UUID, ForeignKey("source_asset.id"), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("ontology_hash", String(64), nullable=False),
    Column("scope_completeness", String, nullable=False),
    Column("actor_kind", String, nullable=False),
    Column("actor_id", String, nullable=False),
    Column("output_truncated", Boolean, nullable=False),
    Column("unresolved_regions", JSON, nullable=False, server_default="[]"),
    constraints=(
        UniqueConstraint("asset_id", "revision"),
        CheckConstraint("NOT output_truncated OR scope_completeness <> 'exhaustive_for_scope'"),
    ),
)
similarity_relation = table(
    "similarity_relation",
    Column("left_asset_id", UUID, ForeignKey("source_asset.id"), nullable=False),
    Column("right_asset_id", UUID, ForeignKey("source_asset.id"), nullable=False),
    Column("reason", String, nullable=False),
    Column("evidence", Text, nullable=False),
    Column("embedding_model", String),
    Column("algorithm_version", String, nullable=False),
)
borderline_review = table(
    "borderline_review",
    Column("left_asset_id", UUID, ForeignKey("source_asset.id"), nullable=False),
    Column("right_asset_id", UUID, ForeignKey("source_asset.id"), nullable=False),
    Column("metric", String, nullable=False),
    Column("value", Text, nullable=False),
    Column("review_state", String, nullable=False, server_default="pending"),
    Column("algorithm_version", String, nullable=False),
)
source_split_assignment = table(
    "source_split_assignment",
    Column("assignment_version", String, nullable=False),
    Column("asset_id", UUID, ForeignKey("source_asset.id"), nullable=False),
    Column("group_id", String, nullable=False),
    Column("split", String, nullable=False),
    Column("seed", Integer, nullable=False),
    Column("algorithm", String, nullable=False),
    Column("thresholds", JSON, nullable=False),
    Column("reason", Text, nullable=False),
    Column("locked", Boolean, nullable=False),
    constraints=(
        UniqueConstraint("assignment_version", "asset_id"),
        CheckConstraint("split IN ('train','val','test')"),
    ),
)
canonical_dataset_release = table(
    "canonical_dataset_release",
    Column("release_key", String, unique=True, nullable=False),
    Column("project_id", UUID, ForeignKey("project.id"), nullable=False),
    Column("ontology_id", UUID, ForeignKey("ontology_release.id"), nullable=False),
    Column("assignment_version", String, nullable=False),
    Column("sample_revisions", JSON, nullable=False),
    Column("ontology_hash", String(64), nullable=False),
    Column("excluded_asset_ids", JSON, nullable=False),
    Column("hashes", JSON, nullable=False),
    Column("frozen", Boolean, nullable=False),
)
preprocessing_recipe = table(
    "preprocessing_recipe",
    Column("recipe_key", String, unique=True, nullable=False),
    Column("policy", JSON, nullable=False),
    Column("immutable", Boolean, nullable=False),
)
derived_sample = table(
    "derived_sample",
    Column("variant_id", UUID, ForeignKey("yolo_dataset_variant.id"), nullable=False),
    Column("source_id", UUID, ForeignKey("source_asset.id"), nullable=False),
    Column("crop", JSON, nullable=False),
    Column("transform", JSON, nullable=False),
    Column("image_sha256", String(64), nullable=False),
    Column("image_key", String, nullable=False),
    Column("label_sha256", String(64), nullable=False),
    constraints=(UniqueConstraint("source_id", "variant_id"),),
)
yolo_dataset_variant = table(
    "yolo_dataset_variant",
    Column("release_id", UUID, ForeignKey("canonical_dataset_release.id"), nullable=False),
    Column("recipe_id", UUID, ForeignKey("preprocessing_recipe.id"), nullable=False),
    Column("class_map", JSON, nullable=False),
    Column("file_manifest", JSON, nullable=False),
    Column("validated", Boolean, nullable=False),
    Column("non_empty_val", Boolean, nullable=False),
    Column("platform_mapping", JSON, nullable=False, server_default="{}"),
    constraints=(
        UniqueConstraint("release_id", "recipe_id"),
        CheckConstraint("validated AND non_empty_val"),
    ),
)
canonical_event = table(
    "canonical_event",
    Column("event_type", String, nullable=False),
    Column("aggregate_id", String, nullable=False),
    Column("expected_revision", BigInteger, nullable=False),
    Column("payload", JSON, nullable=False),
    Column("actor_kind", String, nullable=False),
    Column("actor_id", String, nullable=False),
)
