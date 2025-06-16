# apps/core/models.py
from django.db import models
import uuid


class TimeStampedModel(models.Model):
    """
    Abstract base class that provides created_at and updated_at fields
    """
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
   
    class Meta:
        abstract = True


class UUIDModel(models.Model):
    """
    Abstract base class that provides UUID primary key
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
   
    class Meta:
        abstract = True


class BaseModel(TimeStampedModel, UUIDModel):
    """
    Base model with UUID primary key and timestamps
    """
    class Meta:
        abstract = True


class SoftDeleteManager(models.Manager):
    """
    Manager for soft-deleted models
    """
    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


class SoftDeleteModel(models.Model):
    """
    Abstract model for soft deletion
    """
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
   
    objects = SoftDeleteManager()
    all_objects = models.Manager()
   
    class Meta:
        abstract = True
   
    def soft_delete(self):
        """Soft delete the instance"""
        from django.utils import timezone
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save()
   
    def restore(self):
        """Restore soft deleted instance"""
        self.is_deleted = False
        self.deleted_at = None
        self.save()