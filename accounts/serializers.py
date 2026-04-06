from rest_framework import serializers
from django.contrib.auth import authenticate
from django.utils import timezone
from .models import CustomUser, CustomUserProfile, QuestionUsage, UserEntitlement

class CustomUserSerializer(serializers.ModelSerializer):
    plan_type = serializers.SerializerMethodField()
    lifetime_unlocked = serializers.SerializerMethodField()
    has_unlimited_access = serializers.SerializerMethodField()
    questions_remaining_today = serializers.SerializerMethodField()

    class Meta:
        model = CustomUser
        fields = [
            'id',
            'username',
            'email',
            'plan_type',
            'lifetime_unlocked',
            'has_unlimited_access',
            'questions_remaining_today',
        ]

    def _get_entitlement(self, obj):
        entitlement = getattr(obj, 'entitlement', None)
        if entitlement is None:
            entitlement, _ = UserEntitlement.objects.get_or_create(user=obj)
        return entitlement

    def get_plan_type(self, obj):
        return self._get_entitlement(obj).plan_type

    def get_lifetime_unlocked(self, obj):
        return self._get_entitlement(obj).lifetime_unlocked

    def get_has_unlimited_access(self, obj):
        return self._get_entitlement(obj).has_unlimited_access

    def get_questions_remaining_today(self, obj):
        entitlement = self._get_entitlement(obj)
        if entitlement.has_unlimited_access:
            return None

        question_count = (
            QuestionUsage.objects.filter(user=obj, date=timezone.localdate())
            .values_list('question_count', flat=True)
            .first()
            or 0
        )
        return max(UserEntitlement.FREE_DAILY_QUESTION_LIMIT - question_count, 0)

class CustomUserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomUserProfile
        fields = ['bio', 'profile_img', 'created_at']
                  
class UserRegistrationSerializer(serializers.ModelSerializer):
    password1 = serializers.CharField(write_only = True)
    password2 = serializers.CharField(write_only = True)

    class Meta:
        model = CustomUser
        fields = ('email', 'username', 'password1', 'password2', 'id')
        extra_kwargs = {'password1':{'write_only': True},
                        'password2':{'write_only': True}}
        
    def validate(self, attrs):
        if attrs['password1'] != attrs['password2']:
            raise serializers.ValidationError('Passwords do not match')
        password = attrs.get('password1', '')
        if len(password) < 8:
            raise serializers.ValidationError('Password needs to be more than 8 characters')
        return attrs
    
    def create(self, validated_data):
        password = validated_data.pop('password1')
        validated_data.pop('password2')
        return CustomUser.objects.create_user(**validated_data, password=password)

class UserLoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only = True)

    def validate(self, attrs):
        user = authenticate(**attrs)
        if user and user.is_active:
            return user
        raise serializers.ValidationError('User is not active / does not exist')