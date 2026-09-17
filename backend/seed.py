"""Load a small set of sample rows so the dashboard and charts have something
to show on a fresh install.

Run with `python seed.py`. It refuses to run against a database that already
has projects, so it can never overwrite real work.
"""

import random
from datetime import date, datetime, timedelta

from sqlalchemy import select

from app import models
from app.db import SessionLocal, init_db


def seed():
    init_db()
    db = SessionLocal()
    try:
        if db.execute(select(models.Project).limit(1)).first():
            print("Database already has projects -- nothing seeded.")
            return

        today = date.today()
        rng = random.Random(7)  # fixed seed: the sample data is the same every time

        projects = [
            models.Project(
                name="Shift-scheduling demand forecast",
                summary="Weekly headcount forecast for the concentrator, to cut overtime spend.",
                status="active",
                category="build",
                priority="high",
                start_date=today - timedelta(days=38),
                target_date=today + timedelta(days=24),
                objective="Give planning a defensible weekly number instead of a gut feel.",
                definition_of_done=(
                    "Backtested MAPE under 12% and a dashboard planning actually opens."
                ),
                stakeholder="Planning superintendent",
                tech_stack="Python, Prophet, Power BI",
            ),
            models.Project(
                name="Document Q&A over SOP library",
                summary="Retrieval-augmented assistant over standard operating procedures.",
                status="active",
                category="research",
                priority="medium",
                start_date=today - timedelta(days=21),
                target_date=today + timedelta(days=45),
                objective="Cut the time it takes to find the right clause in an SOP.",
                definition_of_done="Answers 40 benchmark questions with a cited source.",
                stakeholder="Safety & compliance",
                tech_stack="Python, LangChain, Azure OpenAI",
            ),
            models.Project(
                name="Anomaly detection on plant sensor feed",
                summary="Flag drifting sensors before they trip a downstream alarm.",
                status="planning",
                category="analysis",
                priority="medium",
                start_date=today - timedelta(days=6),
                target_date=today + timedelta(days=70),
                stakeholder="Process engineering",
                tech_stack="Python, scikit-learn",
            ),
            models.Project(
                name="ML fundamentals — Stanford CS229",
                summary="Working through the course to shore up the theory.",
                status="active",
                category="learning",
                priority="low",
                start_date=today - timedelta(days=52),
                progress_override=45,
            ),
            models.Project(
                name="Monthly reporting pack automation",
                summary="Replaced the manual Excel refresh with a scheduled job.",
                status="done",
                category="ops",
                priority="medium",
                start_date=today - timedelta(days=95),
                target_date=today - timedelta(days=30),
                completed_at=datetime.now() - timedelta(days=28),
                retro="Scoping the output format first saved a whole rebuild. Do that again.",
                stakeholder="Finance",
            ),
        ]
        db.add_all(projects)
        db.flush()

        forecast, docqa, anomaly, course, reporting = projects

        milestones = [
            models.Milestone(
                project_id=forecast.id,
                title="Historical data cleaned and joined",
                status="done",
                due_date=today - timedelta(days=20),
                completed_at=datetime.now() - timedelta(days=19),
                position=1,
            ),
            models.Milestone(
                project_id=forecast.id,
                title="Baseline model backtested",
                due_date=today + timedelta(days=5),
                position=2,
            ),
            models.Milestone(
                project_id=forecast.id,
                title="Handed to planning for review",
                due_date=today + timedelta(days=24),
                position=3,
            ),
            models.Milestone(
                project_id=docqa.id,
                title="Benchmark question set written",
                due_date=today + timedelta(days=3),
                position=1,
            ),
            models.Milestone(
                project_id=docqa.id,
                title="Retrieval evaluated end to end",
                due_date=today + timedelta(days=30),
                position=2,
            ),
        ]
        db.add_all(milestones)
        db.flush()

        tasks = [
            models.Task(project_id=forecast.id, milestone_id=milestones[1].id,
                        title="Backtest the seasonal baseline", status="in_progress",
                        priority="high", due_date=today + timedelta(days=2),
                        estimate_hours=6),
            models.Task(project_id=forecast.id, milestone_id=milestones[1].id,
                        title="Write the MAPE evaluation script", status="todo",
                        priority="high", due_date=today - timedelta(days=1),
                        estimate_hours=3),
            models.Task(project_id=forecast.id, title="Clean the 2024 shift roster export",
                        status="done", priority="medium",
                        completed_at=datetime.now() - timedelta(days=22)),
            models.Task(project_id=forecast.id, title="Join weather data to the roster",
                        status="done", priority="low",
                        completed_at=datetime.now() - timedelta(days=15)),
            models.Task(project_id=forecast.id, title="Draft the Power BI page layout",
                        status="todo", priority="medium",
                        due_date=today + timedelta(days=12), estimate_hours=4),
            models.Task(project_id=docqa.id, milestone_id=milestones[3].id,
                        title="Write 40 benchmark questions with expected sources",
                        status="in_progress", priority="high",
                        due_date=today, estimate_hours=5),
            models.Task(project_id=docqa.id, title="Chunk and embed the SOP PDFs",
                        status="done", priority="medium",
                        completed_at=datetime.now() - timedelta(days=9)),
            models.Task(project_id=docqa.id, title="Get read access to the SharePoint SOP folder",
                        status="blocked", priority="high",
                        blocked_reason="waiting on IT service request",
                        due_date=today - timedelta(days=4)),
            models.Task(project_id=anomaly.id, title="Pull a month of sensor history",
                        status="todo", priority="medium",
                        due_date=today + timedelta(days=6), estimate_hours=2),
            models.Task(project_id=anomaly.id, title="Read up on seasonal-hybrid ESD",
                        status="todo", priority="low", estimate_hours=3),
            models.Task(project_id=course.id, title="Finish lecture 7 problem set",
                        status="todo", priority="low",
                        due_date=today + timedelta(days=4)),
            models.Task(project_id=reporting.id, title="Schedule the monthly job",
                        status="done", priority="medium",
                        completed_at=datetime.now() - timedelta(days=29)),
            models.Task(title="Book time with the data engineering lead",
                        status="todo", priority="medium",
                        due_date=today + timedelta(days=1)),
        ]
        # A task has to exist before it can be finished. Without this the
        # seeded rows would all be created "now" and closed in the past, which
        # makes cycle time and the opened-vs-closed chart nonsense.
        now = datetime.now()
        for task in tasks:
            if task.completed_at:
                task.created_at = task.completed_at - timedelta(
                    days=rng.randint(2, 18), hours=rng.randint(0, 8)
                )
            else:
                task.created_at = now - timedelta(days=rng.randint(0, 40))

        db.add_all(tasks)
        db.flush()

        # Eight weeks of plausible daily logs, weekdays only, so the trend and
        # throughput charts have a shape rather than a single column.
        category_weights = {
            forecast.id: ["build", "analysis", "build", "meeting"],
            docqa.id: ["research", "build", "research", "meeting"],
            anomaly.id: ["research", "analysis"],
            course.id: ["learning"],
        }
        logs = []
        for offset in range(56):
            day = today - timedelta(days=offset)
            if day.weekday() >= 5:
                continue
            for project_id, categories in category_weights.items():
                if rng.random() < 0.45:
                    continue
                logs.append(
                    models.TimeLog(
                        project_id=project_id,
                        work_date=day,
                        hours=round(rng.uniform(0.5, 3.5) * 4) / 4,
                        category=rng.choice(categories),
                    )
                )
            if rng.random() < 0.3:
                logs.append(
                    models.TimeLog(
                        work_date=day,
                        hours=round(rng.uniform(0.25, 1.5) * 4) / 4,
                        category="admin",
                        note="email, stand-ups, misc",
                    )
                )
        db.add_all(logs)

        db.add_all([
            models.Note(project_id=forecast.id, kind="decision", pinned=True,
                        title="Weekly, not daily",
                        body="Forecasting daily headcount was noisier than the signal. "
                             "Planning commits weekly anyway, so the model forecasts weekly."),
            models.Note(project_id=docqa.id, kind="result",
                        title="Chunk size matters more than the embedding model",
                        body="512-token chunks with 64 overlap beat 1024 clearly. "
                             "Swapping the embedding model changed almost nothing."),
            models.Note(project_id=docqa.id, kind="blocker",
                        body="SharePoint access is the whole critical path right now."),
        ])

        db.add_all([
            models.Link(project_id=docqa.id, kind="paper",
                        title="Dense Passage Retrieval for Open-Domain QA",
                        url="https://arxiv.org/abs/2004.04906"),
            models.Link(project_id=forecast.id, kind="doc",
                        title="Forecasting: Principles and Practice",
                        url="https://otexts.com/fpp3/"),
            models.Link(project_id=course.id, kind="doc", title="CS229 course notes",
                        url="https://cs229.stanford.edu/"),
        ])

        db.commit()
        print(
            f"Seeded {len(projects)} projects, {len(tasks)} tasks, "
            f"{len(milestones)} milestones and {len(logs)} time entries."
        )
    finally:
        db.close()


if __name__ == "__main__":
    seed()
