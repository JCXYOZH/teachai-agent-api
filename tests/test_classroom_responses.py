import json
from typing import Any, Dict, List

from httpx import AsyncClient
from support import register_and_verify

from app.core.config import settings
from app.services.classrooms import ClassroomService

API = settings.api_v1_prefix

RESPONSE_PAYLOAD: Dict[str, Any] = {
    "name": "作答课堂",
    "description": "用测验和项目检查理解。",
    "scenes": [
        {
            "type": "quiz",
            "title": "检查理解",
            "narration": "先独立作答，再提交。",
            "questions": [
                {
                    "question": "1 + 1 等于几？",
                    "options": [
                        {"label": "2", "value": "A"},
                        {"label": "3", "value": "B"},
                    ],
                    "answer": ["A"],
                    "analysis": "基本加法。",
                },
                {
                    "question": "选出所有偶数。",
                    "options": [
                        {"label": "2", "value": "A"},
                        {"label": "3", "value": "B"},
                        {"label": "4", "value": "C"},
                    ],
                    "answer": ["A", "C"],
                    "analysis": "2 和 4 是偶数。",
                },
                {
                    "question": "用自己的话说说什么是极限。",
                    "options": [],
                    "answer": ["无限接近某个确定的值"],
                    "analysis": "抓住趋近与目标值。",
                },
            ],
        },
        {
            "type": "pbl",
            "title": "小项目",
            "narration": "现在动手做一个最小闭环。",
            "project": {
                "title": "极限观察记录",
                "description": "记录三次取值并总结规律。",
                "milestones": [
                    {
                        "title": "采集与总结",
                        "microtasks": [
                            {"title": "记录三次取值", "completionCriteria": "三行数据"},
                            {"title": "写出结论", "completionCriteria": "一句话结论"},
                        ],
                    }
                ],
            },
        },
    ],
}


async def register_user(
    client: AsyncClient, email: str = "responses@example.com"
) -> Dict[str, str]:
    response = await register_and_verify(client, email, display_name="Responses")
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def build_classroom(
    client: AsyncClient, headers: Dict[str, str], monkeypatch: Any
) -> Dict[str, Any]:
    course = await client.post(
        f"{API}/courses",
        headers=headers,
        json={"title": "极限", "description": "入门"},
    )
    assert course.status_code == 201, course.text
    course_id = course.json()["id"]

    outline = await client.put(
        f"{API}/courses/{course_id}/outline",
        headers=headers,
        json={
            "course_title": "极限",
            "language_directive": "用简体中文教学。",
            "scenes": [{"type": "quiz", "title": "检查理解", "key_points": []}],
        },
    )
    assert outline.status_code == 200, outline.text

    async def fake_call(
        self: ClassroomService,
        resolved: Any,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        return json.dumps(RESPONSE_PAYLOAD, ensure_ascii=False)

    monkeypatch.setattr(ClassroomService, "_call_provider", fake_call)

    job = await client.post(
        f"{API}/courses/{course_id}/classroom/generate", headers=headers, json={}
    )
    assert job.status_code == 201, job.text

    classroom = await client.get(f"{API}/courses/{course_id}/classroom", headers=headers)
    assert classroom.status_code == 200, classroom.text
    return {"course_id": course_id, "classroom": classroom.json()}


async def test_submit_grades_questions_and_project_tasks(
    client: AsyncClient, monkeypatch: Any
) -> None:
    headers = await register_user(client)
    built = await build_classroom(client, headers, monkeypatch)
    course_id = built["course_id"]
    scenes: List[Dict[str, Any]] = built["classroom"]["scenes"]
    quiz_scene = scenes[0]
    pbl_scene = scenes[1]
    question_ids = [question["id"] for question in quiz_scene["content"]["questions"]]
    microtask_ids = [
        task["id"]
        for task in pbl_scene["content"]["project"]["milestones"][0]["microtasks"]
    ]

    submitted = await client.put(
        f"{API}/courses/{course_id}/classroom/scenes/{quiz_scene['id']}/responses",
        headers=headers,
        json={
            "responses": [
                {"kind": "question", "target_id": question_ids[0], "answer": ["A"]},
                {"kind": "question", "target_id": question_ids[1], "answer": ["A"]},
                {
                    "kind": "question",
                    "target_id": question_ids[2],
                    "answer": "极限就是无限接近一个确定的值",
                },
            ]
        },
    )
    assert submitted.status_code == 200, submitted.text
    body = submitted.json()
    assert [result["score"] for result in body["results"]] == [1, 0, None]
    assert [result["correct"] for result in body["results"]] == [True, False, None]
    assert body["score"] == 1
    assert body["max_score"] == 2
    assert body["results"][2]["reference_answer"] == ["无限接近某个确定的值"]

    task_done = await client.put(
        f"{API}/courses/{course_id}/classroom/scenes/{pbl_scene['id']}/responses",
        headers=headers,
        json={
            "responses": [
                {
                    "kind": "microtask",
                    "target_id": microtask_ids[0],
                    "answer": {"completed": True},
                }
            ]
        },
    )
    assert task_done.status_code == 200, task_done.text
    assert task_done.json()["results"][0]["correct"] is True

    repeated = await client.put(
        f"{API}/courses/{course_id}/classroom/scenes/{pbl_scene['id']}/responses",
        headers=headers,
        json={
            "responses": [
                {
                    "kind": "microtask",
                    "target_id": microtask_ids[0],
                    "answer": {"completed": False},
                }
            ]
        },
    )
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["results"][0]["correct"] is False

    restored = await client.get(
        f"{API}/courses/{course_id}/classroom/responses", headers=headers
    )
    assert restored.status_code == 200, restored.text
    payload = restored.json()
    assert payload["stage_id"] == built["classroom"]["id"]
    assert len(payload["responses"]) == 4
    stored = {record["target_id"]: record for record in payload["responses"]}
    assert stored[microtask_ids[0]]["answer"] == {"completed": False}
    assert stored[question_ids[1]]["score"] == 0


async def test_responses_are_owner_scoped_and_validated(
    client: AsyncClient, monkeypatch: Any
) -> None:
    headers = await register_user(client, "response-owner@example.com")
    other_headers = await register_user(client, "response-other@example.com")
    built = await build_classroom(client, headers, monkeypatch)
    course_id = built["course_id"]
    quiz_scene = built["classroom"]["scenes"][0]
    question_id = quiz_scene["content"]["questions"][0]["id"]
    endpoint = (
        f"{API}/courses/{course_id}/classroom/scenes/{quiz_scene['id']}/responses"
    )

    foreign = await client.put(
        endpoint,
        headers=other_headers,
        json={
            "responses": [{"kind": "question", "target_id": question_id, "answer": ["A"]}]
        },
    )
    assert foreign.status_code == 404
    assert foreign.json()["error"]["code"] == "CLASSROOM_SCENE_NOT_FOUND"

    unknown = await client.put(
        endpoint,
        headers=headers,
        json={
            "responses": [{"kind": "question", "target_id": "missing", "answer": ["A"]}]
        },
    )
    assert unknown.status_code == 400
    assert unknown.json()["error"]["code"] == "CLASSROOM_TARGET_NOT_FOUND"

    invalid = await client.put(
        endpoint,
        headers=headers,
        json={
            "responses": [
                {"kind": "question", "target_id": question_id, "answer": {"completed": True}}
            ]
        },
    )
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "CLASSROOM_ANSWER_INVALID"

    malformed = await client.put(
        endpoint,
        headers=headers,
        json={"responses": [{"kind": "question", "target_id": question_id, "answer": 42}]},
    )
    assert malformed.status_code == 422

    empty = await client.put(
        endpoint, headers=headers, json={"responses": []}
    )
    assert empty.status_code == 422
