from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.database.session import get_db
from app.models.user import User
from app.repositories.document_repository import DocumentRepository
from app.repositories.knowledge_repository import KnowledgeRepository
from app.repositories.project_repository import ProjectRepository
from app.schemas.project import (
    ProjectCreateRequest,
    ProjectResponse,
    ProjectUpdateRequest,
)


# 四类**面试会用到**的材料及其中文名。
#
# 与 `InterviewContextBuilder.DOCUMENT_TYPES` 同一口径：
# 面试的上下文是按这四类分别检索的，因此"资料齐不齐"只该按它们算。
DOCUMENT_TYPE_LABELS: dict[str, str] = {
    "resume": "简历",
    "jd": "岗位要求",
    "project": "项目材料",
    "code": "代码",
}

# 知识图谱实体种类的中文名（展示用）。
KNOWLEDGE_ENTITY_TYPE_LABELS: dict[str, str] = {
    "person": "人物",
    "skill": "技能",
    "technology": "技术",
    "system": "系统",
    "organization": "组织",
    "role": "角色",
    "experience": "经历",
    "achievement": "成果",
    "other": "其他",
}


router = APIRouter(
    prefix="/api/projects",
    tags=["Projects"],
)


@router.post(
    "",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_project(
    data: ProjectCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):

    project_repository = ProjectRepository(
        db
    )

    project = await project_repository.create(
        name=data.name,
        description=data.description,
        owner_id=current_user.id,
    )

    return project


@router.get(
    "",
    response_model=list[ProjectResponse],
)
async def get_my_projects(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取当前用户的所有项目"""

    project_repository = ProjectRepository(
        db
    )

    projects = await project_repository.get_by_owner_id(
        current_user.id
    )

    return projects

@router.get(
    "/{project_id}",
    response_model=ProjectResponse,
)
async def get_project_detail(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取项目详情"""

    project_repository = ProjectRepository(
        db
    )

    project = await project_repository.get_by_id_and_owner(
        project_id=project_id,
        owner_id=current_user.id,
    )

    if not project:

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目不存在",
        )

    return project

@router.get("/{project_id}/readiness")
async def get_project_readiness(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """资料体检：这个项目的资料能不能支撑一场有依据的面试。

    ## 它解决什么问题

    面试只能围绕**已索引**的资料提问。而"已索引"是用户看不到的
    状态：文件传上去了、列表里也有，但若那个类型索引失败或还没
    索引完，面试就检索不到它 —— 于是问题只能围绕别的材料，
    而用户完全不知道发生了什么。

    实测的典型情况：某项目只有 `resume` 一类已索引，
    于是整场面试都在问简历，岗位要求与项目材料从未被问到 ——
    但那看起来像"面试质量不行"，而不是"资料没准备好"。

    ## 为什么标注 `missing_types` 而不只是列数量

    用户看到"0 份岗位要求"时，未必知道**这会让面试变差**。
    因此除了数量，还要给一句**因果说明**（`advice`）。
    """

    project = await ProjectRepository(db).get_by_id_and_owner(
        project_id=project_id,
        owner_id=current_user.id,
    )

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目不存在",
        )

    documents = await DocumentRepository(db).get_by_project(
        project_id=project_id
    )

    # 只统计四类**面试会用到**的材料。
    #
    # `other` 不在其中：面试的上下文体检是按这四类分别检索的
    # （见 `InterviewContextBuilder.DOCUMENT_TYPES`），
    # 把 other 算进来会让"资料齐不齐"看起来比实际乐观。
    counts = {
        document_type: {"total": 0, "indexed": 0}
        for document_type in DOCUMENT_TYPE_LABELS
    }

    # 「其他」类资料单独计数：它不影响 `usable`，但会让用户
    # 困惑"我明明传了资料，为什么说没有"。
    other_total = 0
    other_indexed = 0

    for document in documents:
        if document.document_type == "other":
            other_indexed += 1 if document.status == "embedded" else 0
            other_total += 1
            continue

        bucket = counts.get(document.document_type)

        if bucket is None:
            continue

        bucket["total"] += 1

        if document.status == "embedded":
            bucket["indexed"] += 1

    types = [
        {
            "type": document_type,
            "label": label,
            "total": counts[document_type]["total"],
            "indexed": counts[document_type]["indexed"],
        }
        for document_type, label in DOCUMENT_TYPE_LABELS.items()
    ]

    indexed_types = [
        item["label"] for item in types if item["indexed"] > 0
    ]
    missing_types = [
        item["label"]
        for item in types
        if item["indexed"] == 0
    ]

    if not indexed_types:
        advice = (
            "还没有任何已索引的资料。现在开始面试只能出通用题 —— "
            "无法针对你的经历提问。"
        )
    elif missing_types:
        advice = (
            f"目前只有「{'、'.join(indexed_types)}」已索引，"
            f"面试只能围绕这部分材料提问。"
            f"补上「{'、'.join(missing_types)}」后，"
            "问题会更贴近目标岗位。"
        )
    else:
        advice = "四类资料都已索引，面试会分别考察它们。"

    # ⚠️ 「其他」类型的资料**完全不参与面试出题**。
    #
    # 面试的上下文按四类分别检索（`InterviewContextBuilder.
    # DOCUMENT_TYPES`），`other` 不在其中。因此用户把文件标成
    # 「其他」时，它虽然索引成功、在资料列表里也显示
    # "已索引·可检索"，但**面试永远检索不到它**。
    #
    # 实测：有两个项目各有一份已索引的 `other` 资料，体检却显示
    # "还没有任何已索引的资料" —— 若不解释，用户会以为这是 bug，
    # 而实际是"传的时候选错了类型"。
    if other_indexed:
        advice += (
            f" 另有 {other_indexed} 份标记为「其他」的资料"
            "不参与面试出题 —— 面试只使用简历、岗位要求、"
            "项目材料、代码这四类。若它们其实是其中某一类，"
            "请修改资料类型。"
        )

    return {
        "types": types,
        "indexed_types": indexed_types,
        "missing_types": missing_types,
        "usable": bool(indexed_types),
        # 「其他」类资料的计数单独给出：它影响可用性，却不在四类里。
        "other_total": other_total,
        "other_indexed": other_indexed,
        "advice": advice,
    }


@router.get("/{project_id}/knowledge-graph")
async def get_project_knowledge_graph(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """这个项目的知识图谱（资料抽出的实体与关系）。

    ## 它是什么

    资料不再只是"被检索的文本"—— 它们变成了**可见的知识网络**：
    哪些技术被提到、它们之间是什么关系、哪些是核心节点。
    这是"资料 → 面试"之外的第二条消费路径。

    ## 为什么是只读快照而不是实时抽取

    抽取要过模型（每段一次调用）。因此图谱由
    `scripts.extract_knowledge` 按需生成，端点只负责读 ——
    实时抽取会让打开项目页慢十几秒，还多花钱。

    图谱为空（还没跑过抽取）时 `entities` 为空列表，
    前端整块不显示 —— 而不是显示"知识图谱加载失败"。
    """

    project = await ProjectRepository(db).get_by_id_and_owner(
        project_id=project_id,
        owner_id=current_user.id,
    )

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目不存在",
        )

    entities, relations = await KnowledgeRepository(db).get_graph(
        project_id=project_id
    )

    # 实体名 → id（关系里只有 id，前端展示要名字）
    name_by_id = {entity.id: entity.name for entity in entities}

    # degree（连接数）在 Python 里数 —— 数据量见 get_graph 的说明
    degree: dict[int, int] = {}

    for relation in relations:
        degree[relation.subject_id] = (
            degree.get(relation.subject_id, 0) + 1
        )
        degree[relation.object_id] = (
            degree.get(relation.object_id, 0) + 1
        )

    return {
        "entities": [
            {
                "id": entity.id,
                "name": entity.name,
                "type": entity.entity_type,
                "type_label": KNOWLEDGE_ENTITY_TYPE_LABELS.get(
                    entity.entity_type, entity.entity_type
                ),
                "degree": degree.get(entity.id, 0),
            }
            # 度数降序、同名稳定 —— 核心节点先被看到
            for entity in sorted(
                entities,
                key=lambda item: (-degree.get(item.id, 0), item.name),
            )
        ],
        "relations": [
            {
                "subject": name_by_id.get(relation.subject_id, "?"),
                "predicate": relation.predicate,
                "object": name_by_id.get(relation.object_id, "?"),
            }
            for relation in relations
        ],
    }


@router.put(
    "/{project_id}",
    response_model=ProjectResponse,
)
async def update_project(
    project_id: int,
    data: ProjectUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):

    project_repository = ProjectRepository(
        db
    )

    project = await project_repository.get_by_id_and_owner(
        project_id=project_id,
        owner_id=current_user.id,
    )

    if not project:

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目不存在",
        )

    update_data = data.model_dump(
        exclude_unset=True
    )

    updated_project = await project_repository.update(
        project,
        **update_data,
    )

    return updated_project

@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_project(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """删除项目"""

    project_repository = ProjectRepository(db)

    # 查询项目，同时确保项目属于当前用户
    project = await project_repository.get_by_id_and_owner(
        project_id=project_id,
        owner_id=current_user.id,
    )

    if not project:

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目不存在",
        )

    # 删除项目
    await project_repository.delete(project)