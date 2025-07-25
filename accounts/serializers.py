from rest_framework import serializers
from .models import CustomUser, CustomUserProfile
from django.contrib.auth import authenticate

class CustomUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomUser
        fields = ['id', 'username', 'email']

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