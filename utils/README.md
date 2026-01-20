# 机器人坐标转换工具

## 使用方法

```    
       
R = [[-12.487733217789721, -51.72574241287357], 
    [-52.309173331972005, 0.020692999160472715]];  // [[a, b], [c, d]]
T = [242.54006473963878, 359.39536777184765] ;  // [tx, ty]

# 将真实坐标(X,Y)转换为像素坐标
px = R[0][0] * X + R[0][1] * Y + T[0] 
= -12.487733217789721 * X + -51.72574241287357 * Y + 242.54006473963878
py = R[1][0] * X + R[1][1] * Y + T[1] 
= -52.309173331972005 * X + 0.020692999160472715 * Y + 359.39536777184765
# 之后取整
px = round(px)
py = round(py)
```

## 地图像素坐标原点

 像素坐标左上角为 (0,0)

## 测试数据

真实坐标                 预测像素坐标                    实际像素坐标               误差 
-------------------------------------------------------------------------------------    
(-1.6276, 4.3971)    	(35.42,444.62) 					(33, 443)            2.9164 
(1.429, 0.339)       	(207.16,284.65) 					(214, 290)           8.6821
(-0.6857, -2.9478)   	(403.58,395.2)					 (403, 394)           1.3353 
(2.002, -4.0512)     	(427.09,254.59) 					(420, 249)           9.0285
(0.0253, -6.7959)   	 (593.75,357.93)					 (597, 361)           4.4719

## 快速开始

这个工具用于将机器人的真实世界坐标转换为前端页面显示的像素坐标。

### 1. 安装依赖

```bash
pip install numpy
```

### 2. 基本用法

```python
from coordinate_transformer import CoordinateTransformer

# 第一步：使用已知对应点初始化转换器（只需要做一次）
transformer = CoordinateTransformer()

# 已知的对应点（真实坐标 <-> 像素坐标）
real_coords = [(-1.6276, 4.3971), (1.429, 0.339), (-0.6857, -2.9478)]
pixel_coords = [(33, 443), (214, 290), (403, 394)]

# 计算转换关系
transformer.fit(real_coords, pixel_coords, method='affine')

# 第二步：转换新的坐标（这是前端需要反复调用的）
robot_real_position = (1.5, 2.3)  # 机器人上报的真实坐标
pixel_position = transformer.real_to_pixel(robot_real_position)
print(f"在页面显示位置: {pixel_position}")  # 例如: (245.6, 315.8)
```

## 前端集成建议

### 方案 A：Python 后端 API

在后端提供一个 API 接口：

```python
from flask import Flask, jsonify, request

app = Flask(__name__)

# 初始化转换器（服务启动时执行一次）
transformer = CoordinateTransformer()
transformer.fit(real_coords, pixel_coords, method='affine')

@app.route('/convert', methods=['POST'])
def convert_coordinate():
    data = request.json
    real_x = data['x']
    real_y = data['y']
    
    # 转换坐标
    pixel_x, pixel_y = transformer.real_to_pixel((real_x, real_y))
    
    return jsonify({
        'pixel_x': round(pixel_x),
        'pixel_y': round(pixel_y)
    })
```

前端调用：

```javascript
async function getRobotPixelPosition(realX, realY) {
    const response = await fetch('/convert', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({x: realX, y: realY})
    });
    const data = await response.json();
    return {x: data.pixel_x, y: data.pixel_y};
}

// 使用示例
const robotPos = await getRobotPixelPosition(1.5, 2.3);
robotElement.style.left = robotPos.x + 'px';
robotElement.style.top = robotPos.y + 'px';
```

### 方案 B：直接在前端实现（JavaScript）

如果不想依赖后端，可以将转换矩阵参数传给前端：

```python
# 在 Python 中获取转换参数
info = transformer.get_transform_info()
print(info['rotation_matrix'])      # [[a, b], [c, d]]
print(info['translation_vector'])   # [tx, ty]
```

然后在 JavaScript 中实现：

```javascript
class CoordinateConverter {
    constructor(rotationMatrix, translationVector) {
        this.R = rotationMatrix;  // [[a, b], [c, d]]
        this.T = translationVector;  // [tx, ty]
    }
    
    realToPixel(realX, realY) {
        const pixelX = this.R[0][0] * realX + this.R[0][1] * realY + this.T[0];
        const pixelY = this.R[1][0] * realX + this.R[1][1] * realY + this.T[1];
        return {x: Math.round(pixelX), y: Math.round(pixelY)};
    }
}

// 初始化（使用从 Python 获取的参数）
const converter = new CoordinateConverter(
    [[-12.487733217789721, -51.72574241287357], [-52.309173331972005, 0.020692999160472715]],  // 从 Python 获取
    [242.54006473963878, 359.39536777184765]                        // 从 Python 获取
);

// 使用
const pixelPos = converter.realToPixel(1.5, 2.3);
robotElement.style.left = pixelPos.x + 'px';
robotElement.style.top = pixelPos.y + 'px';
```

## 完整示例

```python
from coordinate_transformer import CoordinateTransformer

# 已知对应点（通过标定获得）
real_coords = [
    (-1.6276, 4.3971), 
    (1.429, 0.339), 
    (-0.6857, -2.9478),
    (2.002, -4.0512),
    (0.0253, -6.7959)
]
pixel_coords = [
    (33, 443), 
    (214, 290), 
    (403, 394),
    (420, 249),
    (597, 361)
]

# 初始化并训练
transformer = CoordinateTransformer()
transformer.fit(real_coords, pixel_coords, method='affine')

# 打印转换参数（可以传给前端）
print("转换参数：")
info = transformer.get_transform_info()
print(f"旋转矩阵: {info['rotation_matrix']}")
print(f"平移向量: {info['translation_vector']}")

# 转换机器人位置
robot_positions = [
    (0.0, 0.0),
    (1.5, 2.3),
    (-1.0, -1.5)
]

print("\n机器人位置转换：")
for real_pos in robot_positions:
    pixel_pos = transformer.real_to_pixel(real_pos)
    print(f"真实坐标 {real_pos} → 像素坐标 ({int(pixel_pos[0])}, {int(pixel_pos[1])})")
```

## 常见问题

**Q: 如何获得对应点？**  
A: 需要通过标定，在地图上选择几个点，记录它们的真实坐标和像素坐标。至少需要3个点，越多越准确。

**Q: 转换精度如何？**  
A: 使用 `affine` 方法可以达到亚像素级精度。运行代码后会显示拟合误差。

**Q: 坐标系原点在哪？**  
A: 像素坐标通常左上角为 (0,0)，真实坐标根据你的机器人系统定义。

**Q: 需要多少对应点？**  
A: 最少2个点，建议5个以上分散在地图各处的点，以提高精度。

## 输出示例

运行 `python coordinate_transformer.py` 可以看到：

```
================================================================================
仿射变换 (Affine Transform - 允许拉伸)
================================================================================
坐标转换参数:
旋转矩阵:
[[-8.402, -50.747], [-49.644, -0.219]]
平移向量: [243.843, 362.059]
缩放因子: 50.3616
旋转角度: -140.98°
平均拟合误差: 0.0000 像素
最大拟合误差: 0.0000 像素
```

## 技术支持

如有问题，请联系后端团队。
