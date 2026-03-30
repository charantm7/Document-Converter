import uuid
from datetime import date, datetime, timezone

from sqlalchemy import select, exists
from sqlalchemy.orm import Session

from api_gateway.handlers.decorators import handle_db_error
from shared_database.models import User, EmailVerificationToken, PasswordResetToken, APIKey, Jobs


class UserRepository:

    """
    Repository layer for user model,
    Handles all database interaction related to users
    """

    def __init__(self, db: Session):
        self.db = db

    # Queries
    @handle_db_error(stage="get_user_by_id", message="Database error while fetching user by id")
    def get_by_id(self, user_id: uuid.UUID) -> User | None:
        return self.db.get(User, user_id)

    def get_by_email(self, email: str) -> User | None:
        stmt = select(User).where(User.email == email)
        return self.db.execute(stmt).scalar_one_or_none()

    @handle_db_error(stage="get_user_by_username", message="Database error while fetching user by username")
    def get_by_username(self, username: str) -> User | None:
        stmt = select(User).where(User.username == username)
        return self.db.execute(stmt).scalar_one_or_none()

    # Commands

    @handle_db_error(stage="user_creation_failure", message="Database error during user creation")
    def create(self, **fields) -> User:
        user = User(**fields)
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user

    @handle_db_error(stage="email_existence_check", message="Database error while checking email existence")
    def exists_by_email(self, email: str) -> bool:
        stmt = select(exists().where(User.email == email))
        return self.db.execute(stmt).scalar()

    @handle_db_error(stage="update_last_login", message="Failed to update last login")
    def update_last_login(
        self,
        provider: str,
        user: User
    ) -> None:
        user.last_loggin_at = datetime.now(timezone.utc)
        user.last_login_provider = provider
        self.db.commit()

    @handle_db_error(stage="update_email_verification_status", message="Failed to update email verification status")
    def update_email_verification_status(self, user_id: uuid.UUID) -> None:
        user = self.get_by_id(user_id)
        user.is_email_verified = True
        self.db.commit()

    @handle_db_error(stage="update_email_verification_sent_at", message="Failed to update email verification sent at")
    def update_email_verification_sent_at(self, user: User) -> None:
        user.email_verification_sent_at = datetime.now(timezone.utc)
        self.db.commit()

    @handle_db_error(stage="update_email_verified_at", message="Failed to update email verified at")
    def update_email_verified_at(self, user_id: uuid.UUID) -> None:
        user = self.get_by_id(user_id)
        user.email_verified_at = datetime.now(timezone.utc)
        self.db.commit()

    @handle_db_error(stage="update_password", message="Failed to update password")
    def update_password(self, user: User, hashed_password: str) -> None:
        user.hashed_password = hashed_password
        self.db.commit()

    @handle_db_error(stage="update_password_reset_link_sent_at", message="Failed to update the password link sent at ")
    def update_password_reset_link_sent_at(self, user: User) -> None:
        user.password_reset_sent_at = datetime.now(timezone.utc)
        self.db.commit()

    @handle_db_error(stage="update_password_reseted_at", message="Failed to update the password reseted at")
    def update_password_reseted_at(self, user: User) -> None:
        user.password_reseted_at = datetime.now(timezone.utc)
        self.db.commit()

    # Password Token record
    @handle_db_error(stage="create_password_reset_record", message="Failed to create password reset record")
    def create_password_reset_record(self, **fields) -> None:
        record = PasswordResetToken(**fields)
        self.db.add(record)
        self.db.commit()

    @handle_db_error(stage="existence_for_password_reset_token", message="Failed to check the existence of password reset token")
    def is_password_reset_token_exists(self, token: str) -> PasswordResetToken:
        stmt = select(PasswordResetToken).where(
            PasswordResetToken.hashed_token == token)
        return self.db.execute(stmt).scalar_one_or_none()

    @handle_db_error(stage="update_password_reset_token_status", message="Failed to update the password reset token status")
    def update_password_reset_token_status(self, token_record: PasswordResetToken) -> None:
        token_record.used = True
        self.db.commit()


class EmailRepository:
    def __init__(self, db: Session):
        self.db = db

    @handle_db_error(stage="create_email_verification_token_record", message="Failed to create the email verification token record")
    def create(self, **fields) -> None:
        token = EmailVerificationToken(**fields)
        self.db.add(token)
        self.db.commit()

    @handle_db_error(stage="is_email_verification_token_exists", message="Failed at to check the email verification token exists")
    def is_token_exists(self, hashed_token: str) -> EmailVerificationToken:
        stmt = select(EmailVerificationToken).where(
            EmailVerificationToken.hashed_token == hashed_token)
        return self.db.execute(stmt).scalar_one_or_none()

    @handle_db_error(stage="update_email_verification_token_status", message="Failed to update email verification token record")
    def update_token_record_status(self, record: EmailVerificationToken) -> None:
        record.used = True
        self.db.commit()


class APIKeyService:

    def __init__(self, db: Session):
        self.db = db

    def get_by_key(self, hashed_key: str) -> APIKey | None:
        stmt = select(APIKey).where(APIKey.hashed_key == hashed_key)
        return self.db.execute(stmt).scalar_one_or_none()

    def create(self, **field) -> APIKey:
        new_key = APIKey(**field)
        self.db.add(new_key)
        self.db.commit()
        self.db.refresh(new_key)
        return new_key


class JobRepository:
    def __init__(self, db: Session):
        self.db = db

    def create(self, **fields) -> Jobs:
        job_record = Jobs(**fields)
        self.db.add(job_record)
        self.db.commit()
        self.db.refresh(job_record)
        return job_record

    def update_status(self, record: Jobs, status) -> str:
        record.status = status
        self.db.commit()

    @handle_db_error("fetch_job_using_id", "Error while fetching job using id")
    def get_by_job_id(self, id) -> Jobs:
        stmt = select(Jobs).where(Jobs.id == id)
        return self.db.execute(stmt).scalar_one_or_none()

    def update_records(self, record, **kwargs):

        try:
            for key, value in kwargs.items():
                setattr(record, key, value)

            self.db.add(record)
            self.db.commit()
            self.db.refresh(record)

            return record

        except Exception as e:
            self.db.rollback()
            print("DB ERROR:", str(e))
            raise

    @handle_db_error("update_output_url", "Error while updating output_url job record")
    def update_output_url(self, record: Jobs, output_url) -> None:
        record.output_url = output_url
        self.db.commit()
