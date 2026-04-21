library(randomForest)
# Prepare data: train_data with 'class' factor column and numeric feature columns
set.seed(123)
model <- randomForest(class ~ ., data=train_data, ntree=500, importance=TRUE)
# Predict on new data
predictions <- predict(model, newdata=test_data)
# Evaluate
print(confusionMatrix(predictions, test_data$class))