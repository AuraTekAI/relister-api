from rest_framework import serializers

from .models import ExtensionLog


class ExtensionLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExtensionLog
        fields = ['id', 'user', 'log', 'created_at', 'updated_at']
        read_only_fields = ['id', 'user', 'created_at', 'updated_at']
