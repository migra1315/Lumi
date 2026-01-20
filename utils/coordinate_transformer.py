import numpy as np
from typing import List, Tuple


class CoordinateTransformer:
    """
    坐标转换类：计算并应用真实坐标与像素坐标之间的转换关系
    转换关系为: pixel = R * real + T
    其中 R 是旋转矩阵，T 是平移向量
    """
    
    def __init__(self):
        self.rotation_matrix = None  # 2x2 旋转矩阵
        self.translation_vector = None  # 2x1 平移向量
        self.scale = None  # 缩放因子
        self.theta = None  # 旋转角度(弧度)
        
    def fit(self, real_coords: List[Tuple[float, float]], 
            pixel_coords: List[Tuple[float, float]], 
            method: str = 'similarity') -> None:
        """
        根据已知对应点计算转换矩阵
        
        参数:
            real_coords: 真实坐标列表 [(x1, y1), (x2, y2), ...]
            pixel_coords: 像素坐标列表 [(u1, v1), (u2, v2), ...]
            method: 'similarity' (相似变换) 或 'affine' (仿射变换)
        """
        if len(real_coords) != len(pixel_coords):
            raise ValueError("真实坐标和像素坐标的数量必须相同")
        
        if len(real_coords) < 2:
            raise ValueError("至少需要2组对应点")
        
        # 转换为numpy数组
        real_points = np.array(real_coords)  # shape: (n, 2)
        pixel_points = np.array(pixel_coords)  # shape: (n, 2)
        
        if method == 'similarity':
            self._fit_similarity(real_points, pixel_points)
        elif method == 'affine':
            self._fit_affine(real_points, pixel_points)
        else:
            raise ValueError("method必须是'similarity'或'affine'")
        
        # 计算拟合误差
        self._calculate_fit_error(real_points, pixel_points)
    
    def _fit_similarity(self, real_points: np.ndarray, pixel_points: np.ndarray) -> None:
        """
        使用相似变换拟合(保持角度,只有旋转、缩放和平移)
        """
        # 计算质心
        real_centroid = np.mean(real_points, axis=0)
        pixel_centroid = np.mean(pixel_points, axis=0)
        
        # 中心化
        real_centered = real_points - real_centroid
        pixel_centered = pixel_points - pixel_centroid
        
        # 使用最小二乘法求解
        # pixel = s*R*real, 其中s是缩放因子，R是旋转矩阵
        n = len(real_points)
        
        # 构建线性方程组
        A = np.zeros((2*n, 2))
        B = np.zeros(2*n)
        
        for i in range(n):
            x, y = real_centered[i]
            u, v = pixel_centered[i]
            
            # a*x - b*y = u
            A[2*i] = [x, -y]
            B[2*i] = u
            
            # b*x + a*y = v
            A[2*i+1] = [y, x]
            B[2*i+1] = v
        
        # 最小二乘求解
        params, _, _, _ = np.linalg.lstsq(A, B, rcond=None)
        a, b = params
        
        # 计算旋转角度和缩放因子
        self.scale = np.sqrt(a**2 + b**2)
        self.theta = np.arctan2(b, a)
        
        # 构建旋转矩阵(包含缩放)
        self.rotation_matrix = np.array([[a, -b],
                                         [b, a]])
        
        # 计算平移向量: T = pixel_centroid - R * real_centroid
        self.translation_vector = pixel_centroid - self.rotation_matrix @ real_centroid
    
    def _fit_affine(self, real_points: np.ndarray, pixel_points: np.ndarray) -> None:
        """
        使用仿射变换拟合(允许拉伸和剪切)
        """
        n = len(real_points)
        
        # 构建增广矩阵 [x, y, 1]
        real_aug = np.column_stack([real_points, np.ones(n)])
        
        # 分别求解u和v的变换
        # u = a1*x + a2*y + a3
        # v = b1*x + b2*y + b3
        params_u, _, _, _ = np.linalg.lstsq(real_aug, pixel_points[:, 0], rcond=None)
        params_v, _, _, _ = np.linalg.lstsq(real_aug, pixel_points[:, 1], rcond=None)
        
        # 构建变换矩阵
        self.rotation_matrix = np.array([[params_u[0], params_u[1]],
                                         [params_v[0], params_v[1]]])
        self.translation_vector = np.array([params_u[2], params_v[2]])
        
        # 对于仿射变换,计算等效的旋转角度和缩放
        U, S, Vt = np.linalg.svd(self.rotation_matrix)
        self.scale = np.mean(S)
        self.theta = np.arctan2(U[1, 0], U[0, 0])
        
    def _calculate_fit_error(self, real_points: np.ndarray, 
                            pixel_points: np.ndarray) -> None:
        """计算拟合误差"""
        predicted = self.real_to_pixel_batch(real_points)
        errors = np.linalg.norm(predicted - pixel_points, axis=1)
        self.mean_error = np.mean(errors)
        self.max_error = np.max(errors)
        
    def real_to_pixel(self, real_coord: Tuple[float, float]) -> Tuple[float, float]:
        """
        将真实坐标转换为像素坐标
        
        参数:
            real_coord: 真实坐标 (x, y)
            
        返回:
            pixel_coord: 像素坐标 (u, v)
        """
        if self.rotation_matrix is None:
            raise ValueError("请先调用fit()方法计算转换参数")
        
        real_point = np.array(real_coord)
        pixel_point = self.rotation_matrix @ real_point + self.translation_vector
        
        return tuple(pixel_point)
    
    def real_to_pixel_batch(self, real_coords: np.ndarray) -> np.ndarray:
        """
        批量转换真实坐标到像素坐标
        
        参数:
            real_coords: shape (n, 2) 的numpy数组
            
        返回:
            pixel_coords: shape (n, 2) 的numpy数组
        """
        if self.rotation_matrix is None:
            raise ValueError("请先调用fit()方法计算转换参数")
        
        return (self.rotation_matrix @ real_coords.T).T + self.translation_vector
    
    def pixel_to_real(self, pixel_coord: Tuple[float, float]) -> Tuple[float, float]:
        """
        将像素坐标转换为真实坐标(逆变换)
        
        参数:
            pixel_coord: 像素坐标 (u, v)
            
        返回:
            real_coord: 真实坐标 (x, y)
        """
        if self.rotation_matrix is None:
            raise ValueError("请先调用fit()方法计算转换参数")
        
        pixel_point = np.array(pixel_coord)
        # 逆变换: real = R^(-1) * (pixel - T)
        inv_rotation = np.linalg.inv(self.rotation_matrix)
        real_point = inv_rotation @ (pixel_point - self.translation_vector)
        
        return tuple(real_point)
    
    def get_transform_info(self) -> dict:
        """
        获取转换参数信息
        
        返回:
            包含转换参数的字典
        """
        if self.rotation_matrix is None:
            return {"status": "未拟合"}
        
        return {
            "rotation_matrix": self.rotation_matrix.tolist(),
            "translation_vector": self.translation_vector.tolist(),
            "scale": self.scale,
            "rotation_angle_deg": np.degrees(self.theta),
            "rotation_angle_rad": self.theta,
            "mean_fit_error": self.mean_error,
            "max_fit_error": self.max_error
        }
    
    def __str__(self) -> str:
        """打印转换信息"""
        info = self.get_transform_info()
        if "status" in info:
            return info["status"]
        
        return f"""坐标转换参数:
旋转矩阵:
{info['rotation_matrix']}
平移向量: {info['translation_vector']}
缩放因子: {info['scale']:.4f}
旋转角度: {info['rotation_angle_deg']:.2f}°
平均拟合误差: {info['mean_fit_error']:.4f} 像素
最大拟合误差: {info['max_fit_error']:.4f} 像素"""


# 使用示例
if __name__ == "__main__":
    # 已知的对应点
    pixel_coords = [(33, 443), 
                    (214, 290), 
                    (403, 394),
                    (420,249),
                    (597,361)]
    real_coords = [(-1.6276, 4.3971), 
                   (1.429, 0.339), 
                   (-0.6857, -2.9478),
                   (2.002,-4.0512),
                   (0.0253,-6.7959)]
    
    print("\n" + "="*80)
    print("仿射变换 (Affine Transform - 允许拉伸)")
    print("="*80)
    
    # 创建转换器并使用仿射变换拟合
    transformer_aff = CoordinateTransformer()
    transformer_aff.fit(real_coords, pixel_coords, method='affine')
    print(transformer_aff)
    print("\n验证转换精度:")
    print(f"{'真实坐标':<20} {'预测像素坐标':<25} {'实际像素坐标':<20} {'误差'}")
    print("-" * 85)
    
    for real, pixel in zip(real_coords, pixel_coords):
        predicted = transformer_aff.real_to_pixel(real)
        error = np.linalg.norm(np.array(predicted) - np.array(pixel))
        print(f"{str(real):<20} {str(tuple(np.round(predicted, 2))):<25} {str(pixel):<20} {error:.4f}")
    
    print("\n" + "="*80)


   
